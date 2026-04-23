"""FastAPI app entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.admin import router as admin_router
from app.api.metrics import router as metrics_router
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine
from app.core.deps import set_config_service
from app.core.logging import configure_logging
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ConfigService(session_factory, config_cache, secrets_cache, fernet)
    set_config_service(svc)

    listener_config = asyncio.create_task(config_cache.run_listener())
    listener_secrets = asyncio.create_task(secrets_cache.run_listener())

    log.info("startup_ok env=%s", settings.env)
    try:
        yield
    finally:
        listener_config.cancel()
        listener_secrets.cancel()
        for t in (listener_config, listener_secrets):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await redis.aclose()
        await engine.dispose()


app = FastAPI(title="Trading Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(metrics_router)


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
