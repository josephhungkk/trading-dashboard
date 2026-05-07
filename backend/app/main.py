"""FastAPI app entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import admin_instruments
from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.admin_metrics import router as admin_metrics_router
from app.api.bars import router as bars_router  # Task 28: GET /api/bars
from app.api.brokers import router as brokers_router
from app.api.brokers_admin import router as brokers_admin_router
from app.api.capabilities import router as capabilities_router
from app.api.chart_layouts import router as chart_layouts_router
from app.api.contracts import router as contracts_router
from app.api.metrics import router as metrics_router
from app.api.oauth import router as oauth_router
from app.api.orders import fills_router
from app.api.orders import router as orders_router
from app.api.sse import router as sse_router
from app.api.ws_quotes import router as ws_quotes_router
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine
from app.core.deps import set_account_service, set_broker_registry, set_config_service
from app.core.logging import configure_logging
from app.services.bar_service import BarService
from app.services.broker_callback_server import start_backend_callback_server
from app.services.broker_registry_factory import MissingBrokerSecrets, build_broker_registry
from app.services.brokers import AccountService, BrokerDiscoverer, BrokerRegistry
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache
from app.services.oco_orchestrator import OcoOrchestrator
from app.services.order_event_consumer import OrderEventConsumer
from app.services.pending_fills_sweeper import PendingFillsSweeper
from app.services.pending_submit_watchdog import PendingSubmitWatchdog
from app.services.postgres_listen_bridge import PostgresListenBridge
from app.services.quotes.instruments_seed import seed_instruments_from_positions

configure_logging()
log = structlog.get_logger(__name__)


_bar_service: BarService | None = None  # Set in lifespan; read by _run_pre_warm.


async def _run_pre_warm() -> None:
    """Run BarService.pre_warm_active_set in a new session; called by the cron scheduler."""
    if _bar_service is None:
        log.warning("pre_warm.skipped_not_ready")
        return
    async with SessionLocal() as session:
        try:
            await _bar_service.pre_warm_active_set(session)
        except Exception as exc:
            log.error("pre_warm.failed", error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    _app.state.redis = redis
    # Build plain postgresql:// DSN for asyncpg (strip +asyncpg driver prefix)
    _listen_dsn = getattr(settings, "DATABASE_URL_LISTEN", None) or settings.database_url.replace(
        "+asyncpg", "", 1
    )
    bridge = PostgresListenBridge(dsn=_listen_dsn, redis=redis)
    bridge_task: asyncio.Task[None] = asyncio.create_task(bridge.run())
    config_cache = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=300)
    secrets_cache = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=300)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ConfigService(session_factory, config_cache, secrets_cache, fernet)
    set_config_service(svc)
    callback_server = await start_backend_callback_server(svc, session_factory)

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
    oco_orchestrator: OcoOrchestrator | None = None
    oco_orchestrator_task: asyncio.Task[None] | None = None

    try:
        try:
            seeded = await seed_instruments_from_positions(session_factory)
            log.info("instrument_seed.complete", count=seeded)
        except (Exception,) as exc:  # noqa: B013 - project convention keeps tuple form
            log.warning("instrument_seed.failed", exc=str(exc))

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
        oco_orchestrator = OcoOrchestrator(db=session_factory, redis=redis)  # type: ignore[arg-type]  # redis-py Redis satisfies _RedisLike at runtime; Protocol excludes optional kwargs
        oco_orchestrator_task = asyncio.create_task(oco_orchestrator.start())
        log.info("broker_lifespan_started")
    except MissingBrokerSecrets as exc:
        log.warning("broker_lifespan_skipped", reason=str(exc))

    global _bar_service
    bar_service = BarService()
    _bar_service = bar_service
    _app.state.bar_service = bar_service
    await bar_service.start()

    # ── Cron scheduler for periodic bar pre-warming ──────────────────────────
    # TODO: dynamic per-asset-class via market_calendar.py next_close (Phase 10).
    scheduler = AsyncIOScheduler()
    # NYSE close ~21:00 UTC (16:00 ET + 5h)
    scheduler.add_job(_run_pre_warm, CronTrigger(hour=21, minute=5, timezone="UTC"))
    # HKEX close ~08:30 UTC (16:30 HKT - 8h)
    scheduler.add_job(_run_pre_warm, CronTrigger(hour=8, minute=35, timezone="UTC"))
    # FX rollover ~22:00 UTC
    scheduler.add_job(_run_pre_warm, CronTrigger(hour=22, minute=5, timezone="UTC"))
    scheduler.start()
    _app.state.scheduler = scheduler

    # Run once immediately on startup (non-blocking).
    pre_warm_task: asyncio.Task[None] = asyncio.create_task(_run_pre_warm())

    log.info("startup_ok", env=settings.env)
    try:
        yield
    finally:
        # Cancel the initial pre-warm task if still running (codex-default B).
        pre_warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pre_warm_task
        scheduler.shutdown(wait=False)
        await _app.state.bar_service.stop()
        _bar_service = None
        if pending_fills_sweeper is not None:
            await pending_fills_sweeper.stop()
        if pending_fills_task is not None:
            try:
                await pending_fills_task
            except asyncio.CancelledError:
                pass
        if oco_orchestrator is not None:
            await oco_orchestrator.stop()
        if oco_orchestrator_task is not None:
            oco_orchestrator_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await oco_orchestrator_task
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

        bridge.stop()
        bridge_task.cancel()
        await asyncio.gather(bridge_task, return_exceptions=True)
        listener_config.cancel()
        listener_secrets.cancel()
        for t in (listener_config, listener_secrets):
            try:
                await t
            except asyncio.CancelledError:
                pass
        try:
            await callback_server.stop(grace=5)
        except Exception:
            log.exception("callback_server_stop_failed")
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
app.include_router(admin_instruments.router)
app.include_router(brokers_admin_router)
app.include_router(accounts_router)
app.include_router(brokers_router)
app.include_router(capabilities_router)
app.include_router(metrics_router)
app.include_router(orders_router)
app.include_router(fills_router)
app.include_router(contracts_router)
app.include_router(oauth_router)
app.include_router(sse_router)
app.include_router(admin_metrics_router)
app.include_router(ws_quotes_router)
app.include_router(chart_layouts_router)
app.include_router(bars_router)  # Task 28: GET /api/bars cursor pagination


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
