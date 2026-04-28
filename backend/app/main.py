"""FastAPI app entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.contracts import router as contracts_router
from app.api.metrics import router as metrics_router
from app.api.orders import fills_router
from app.api.orders import router as orders_router
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine
from app.core.deps import set_account_service, set_broker_registry, set_config_service
from app.core.logging import configure_logging
from app.services.broker_registry_factory import MissingBrokerSecrets, build_broker_registry
from app.services.brokers import AccountService, BrokerDiscoverer, BrokerRegistry
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache
from app.services.order_event_consumer import OrderEventConsumer
from app.services.pending_fills_sweeper import PendingFillsSweeper
from app.services.pending_submit_watchdog import PendingSubmitWatchdog

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    _app.state.redis = redis
    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ConfigService(session_factory, config_cache, secrets_cache, fernet)
    set_config_service(svc)

    listener_config = asyncio.create_task(config_cache.run_listener())
    listener_secrets = asyncio.create_task(secrets_cache.run_listener())

    broker_registry: BrokerRegistry | None = None
    broker_discoverer: BrokerDiscoverer | None = None
    broker_health_task: asyncio.Task[None] | None = None
    broker_discover_task: asyncio.Task[None] | None = None
    order_consumer: OrderEventConsumer | None = None
    pending_watchdog: PendingSubmitWatchdog | None = None
    pending_fills_sweeper: PendingFillsSweeper | None = None
    pending_fills_task: asyncio.Task[None] | None = None

    try:
        broker_registry = await build_broker_registry(svc)
        broker_discoverer = BrokerDiscoverer(broker_registry, session_factory)
        broker_health_task = asyncio.create_task(broker_registry.health_probe_loop())
        broker_discover_task = asyncio.create_task(broker_discoverer.discover_loop())
        set_broker_registry(broker_registry)
        set_account_service(AccountService(broker_registry, session_factory))

        order_consumer = OrderEventConsumer(broker_registry, session_factory, redis)
        pending_watchdog = PendingSubmitWatchdog(broker_registry, session_factory, order_consumer)
        pending_fills_sweeper = PendingFillsSweeper(session_factory)
        # R9: reconcile orders that transitioned at the broker while the
        # backend was down BEFORE per-account streams open, so synthetic
        # recovery events fire ahead of any live events arriving.
        await pending_watchdog.reconcile_at_startup()
        await order_consumer.start()
        await pending_watchdog.start()
        pending_fills_task = asyncio.create_task(pending_fills_sweeper.run())
        log.info("broker_lifespan_started")
    except MissingBrokerSecrets as exc:
        log.warning("broker_lifespan_skipped reason=%s", exc)

    log.info("startup_ok env=%s", settings.env)
    try:
        yield
    finally:
        if pending_fills_sweeper is not None:
            await pending_fills_sweeper.stop()
        if pending_fills_task is not None:
            try:
                await pending_fills_task
            except asyncio.CancelledError:
                pass
        if pending_watchdog is not None:
            await pending_watchdog.stop()
        if order_consumer is not None:
            await order_consumer.stop()
        if broker_discoverer is not None:
            await broker_discoverer.stop()
        if broker_registry is not None:
            await broker_registry.stop()
        for t in (broker_discover_task, broker_health_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        if broker_registry is not None:
            await broker_registry.close()

        listener_config.cancel()
        listener_secrets.cancel()
        for t in (listener_config, listener_secrets):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await redis.aclose()
        _app.state.redis = None
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
app.include_router(accounts_router)
app.include_router(metrics_router)
app.include_router(orders_router)
app.include_router(fills_router)
app.include_router(contracts_router)


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
