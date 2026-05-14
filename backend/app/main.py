"""FastAPI app entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import os
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

import app.api.telegram as _telegram_api_module
from app.api import admin_instruments
from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.admin_alerts import router as admin_alerts_router
from app.api.admin_metrics import router as admin_metrics_router
from app.api.admin_risk import router as admin_risk_router
from app.api.admin_telegram import router as admin_telegram_router
from app.api.ai import router as ai_router
from app.api.alerts import router as alerts_router
from app.api.bars import router as bars_router  # Task 28: GET /api/bars
from app.api.bars import ws_router as bars_ws_router  # Task 31: WS /ws/bars
from app.api.brokers import router as brokers_router
from app.api.brokers_admin import router as brokers_admin_router
from app.api.capabilities import router as capabilities_router
from app.api.chart_layouts import router as chart_layouts_router
from app.api.contracts import router as contracts_router
from app.api.metrics import router as metrics_router
from app.api.oauth import router as oauth_router
from app.api.orders import fills_router
from app.api.orders import router as orders_router
from app.api.portfolio import router as portfolio_router
from app.api.risk import router as risk_router
from app.api.sizing import router as sizing_router
from app.api.sse import router as sse_router
from app.api.telegram import router as telegram_router
from app.api.ws_ai import router as ws_ai_router
from app.api.ws_alerts import router as ws_alerts_router
from app.api.ws_portfolio import router as ws_portfolio_router
from app.api.ws_quotes import router as ws_quotes_router
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.db import SessionLocal, engine
from app.core.deps import set_account_service, set_broker_registry, set_config_service
from app.core.logging import configure_logging
from app.core.metrics import SCHWAB_REFRESH_TOKEN_AGE_HOURS, SCHWAB_REFRESH_TOKEN_USES_PER_24H
from app.services.ai.orphan_sweeper import run_orphan_sweeper
from app.services.alerts.capabilities import ensure_seeded as ensure_alert_capabilities_seeded
from app.services.alerts.channels.in_app import InAppChannel
from app.services.alerts.channels.telegram import TelegramChannel as TelegramDeliveryChannel
from app.services.alerts.delivery import DeliveryDispatcher
from app.services.alerts.evaluator import AlertsEvaluator
from app.services.alerts.retention import sweep_alert_fire_context
from app.services.alerts.runner import (
    AlertsBarsRedisSubscriber,
    SymbolCache,
    build_index_rebuild_callback,
    build_process_callback,
    run_capability_invalidation_listener,
)
from app.services.balance_snapshot_writer import BalanceSnapshotWriter
from app.services.bar_service import BarService
from app.services.broker_callback_server import start_backend_callback_server
from app.services.broker_registry_factory import MissingBrokerSecrets, build_broker_registry
from app.services.brokers import AccountService, BrokerDiscoverer, BrokerRegistry
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache
from app.services.oco_orchestrator import OcoOrchestrator, OcoOrchestratorImpl
from app.services.order_capability_service import OrderCapabilityService
from app.services.order_event_consumer import OrderEventConsumer
from app.services.pending_fills_sweeper import PendingFillsSweeper
from app.services.pending_submit_watchdog import PendingSubmitWatchdog
from app.services.postgres_listen_bridge import PostgresListenBridge
from app.services.quotes.engine_factory import build_quote_engine
from app.services.quotes.instruments_seed import seed_instruments_from_positions
from app.services.telegram.allowlist import AllowlistService as TelegramAllowlistService
from app.services.telegram.bot import build_dispatcher, telegram_shutdown, telegram_startup
from app.services.telegram.chat import TelegramChat
from app.services.telegram.commands import register_handlers as register_tg_handlers
from app.services.telegram.rate_limiter import TelegramRateLimiter

configure_logging()
log = structlog.get_logger(__name__)


_bar_service: BarService | None = None  # Set in lifespan; read by _run_pre_warm.


async def _update_schwab_token_metrics(redis: Any, db_factory: Any) -> None:
    """HIGH-code-2: populate SCHWAB_REFRESH_TOKEN_AGE_HOURS + USES_PER_24H every 5 min."""
    import json
    from datetime import UTC, datetime

    from sqlalchemy import text as _text

    while True:
        try:
            async with db_factory() as s:
                row = await s.execute(
                    _text(
                        "SELECT value_json FROM app_config"
                        " WHERE namespace='broker' AND key='schwab.refresh_token_issued_at'"
                    )
                )
                issued_raw = row.scalar_one_or_none()
            if issued_raw:
                issued = datetime.fromisoformat(json.loads(issued_raw)).replace(tzinfo=UTC)
                age_hours = (datetime.now(UTC) - issued).total_seconds() / 3600
                SCHWAB_REFRESH_TOKEN_AGE_HOURS.set(age_hours)
            uses = await redis.get("schwab:refresh_uses_24h_count")
            SCHWAB_REFRESH_TOKEN_USES_PER_24H.set(int(uses or 0))
        except Exception as exc:
            log.warning("schwab_token_metrics.update_failed", exc=str(exc))
        await asyncio.sleep(300)


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

    # Phase 11a-A.5 (HIGH-5): bootstrap LiteLLM master-key in Redis so
    # the auth-callback in deploy/litellm/config.yaml sees it. Operator
    # rotates via PUT /api/admin/secrets/ai/litellm_master_key.
    litellm_placeholder = "sk-bootstrap-rotate-me"
    master_key = await svc.reveal_secret("ai", "litellm_master_key")
    if master_key is None:
        master_key = litellm_placeholder
        await svc.set_secret("ai", "litellm_master_key", master_key)
    if master_key == litellm_placeholder:
        # security-reviewer M1: the placeholder is committed to source;
        # surface it every startup so operators can't miss the unrotated
        # default by skim-reading logs once at first boot.
        log.warning(
            "litellm_master_key_placeholder_active",
            remedy="PUT /api/admin/secrets/ai/litellm_master_key with a fresh 32+ char value",
        )
    try:
        await redis.set("ai:litellm_master_key", master_key)
    except Exception as exc:
        # silent-failure H2: this leaves LiteLLM unauthenticatable until
        # an admin rotation reaches Redis. Error-level (not warning) +
        # remedy hint so the operator has an actionable next step.
        log.error(
            "litellm_master_key_redis_set_failed",
            error_class=type(exc).__name__,
            error=str(exc),
            remedy=(
                "check redis health; "
                "call PUT /api/admin/secrets/ai/litellm_master_key once redis is up"
            ),
        )

    from app.services.ai.ollama_health_watcher import OllamaHealthWatcher
    from app.services.ai.wol import HeavyBoxWoL

    _app.state.heavy_wol = HeavyBoxWoL(
        helper_url=os.environ.get("WOL_HELPER_URL", "http://10.10.0.2:11900"),
        heavy_url=os.environ.get("OLLAMA_HEAVY_URL", "http://10.10.0.3:11434"),
    )
    _ollama_watcher = OllamaHealthWatcher(
        hosts={"nuc": os.environ.get("OLLAMA_NUC_URL", "http://10.10.0.2:11434")},
        redis=redis,
    )
    await _ollama_watcher.start()
    _app.state.ollama_health_watcher = _ollama_watcher

    # Phase 11a-B8: AI router stack (services/ai/ core)
    from app.services.ai.cost_ledger import CostLedger
    from app.services.ai.jobs import AIJobStore
    from app.services.ai.rate_limiter import AIRouterRateLimiter
    from app.services.ai.router import LiteLLMClient
    from app.services.ai.secrets import AIProviderKeyCache
    from app.services.config_defaults import DEFAULT_AI_ROUTER_CAPABILITY_MAP

    ai_secrets = AIProviderKeyCache(config_svc=svc)
    _app.state.ai_secrets = ai_secrets
    listener_ai_secrets: asyncio.Task[None] = asyncio.create_task(
        ai_secrets.run_pubsub_listener(redis)
    )

    ai_cost_ledger = CostLedger(session_factory=session_factory)
    await ai_cost_ledger.start()
    _app.state.ai_cost_ledger = ai_cost_ledger

    ai_jobs = AIJobStore(session_factory=session_factory, redis=redis)
    try:
        await ai_jobs.recover_orphans()  # HIGH-8
    except Exception:
        log.exception("ai_jobs_orphan_recovery_failed")
    _app.state.ai_jobs = ai_jobs
    orphan_sweeper_task = asyncio.create_task(run_orphan_sweeper(SessionLocal))

    ai_rate_limiter = AIRouterRateLimiter(
        semaphores={"LOCAL_ONLY": 1, "REASONING": 2, "__default__": 5},
    )
    _app.state.ai_rate_limiter = ai_rate_limiter

    async def _master_key_provider() -> str:
        raw = await redis.get("ai:litellm_master_key")
        if raw is None:
            log.warning(
                "litellm_master_key_missing_from_redis",
                remedy="check redis health; key should be repopulated on next restart",
            )
            return ""
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def _capability_map_provider() -> dict[str, list[dict[str, str]]]:
        override = await svc.get_json("ai_router", "capability_map", default=None)
        return override if isinstance(override, dict) else DEFAULT_AI_ROUTER_CAPABILITY_MAP

    async def _available_providers_provider() -> set[str]:
        # Providers whose api_key is configured in app_secrets.
        # Local Ollama placeholders are always available (no auth needed).
        from app.services.ai.capabilities import LOCAL_PROVIDERS

        available: set[str] = set(LOCAL_PROVIDERS)
        for cloud in ("xai", "gemini", "anthropic", "openai"):
            try:
                key = await svc.reveal_secret("ai_provider", f"{cloud}.api_key")
            except Exception:
                key = None
            if key:
                available.add(cloud)
        return available

    _app.state.ai_router = LiteLLMClient(
        secrets=ai_secrets,
        rate_limiter=ai_rate_limiter,
        cost_ledger=ai_cost_ledger,
        jobs=ai_jobs,
        proxy_url=os.environ.get("LITELLM_PROXY_URL", "http://litellm:4000"),
        master_key_provider=_master_key_provider,
        capability_map_provider=_capability_map_provider,
        available_providers_provider=_available_providers_provider,
    )

    listener_config = asyncio.create_task(config_cache.run_listener())
    listener_secrets = asyncio.create_task(secrets_cache.run_listener())

    # CRIT-1: OrderCapabilityService singleton — shared cache + background listener.
    capability_svc = OrderCapabilityService(redis=redis, db_factory=session_factory)  # type: ignore[arg-type]  # redis-py Redis structurally satisfies RedisLike Protocol
    _app.state.capability_svc = capability_svc
    listener_capability: asyncio.Task[None] = asyncio.create_task(capability_svc.run_listener())

    # Phase 10b.1 H2: VolatilityService singleton (Redis-cached realized-vol + ATR).
    from app.services.volatility_service import VolatilityService

    vol_svc = VolatilityService(db_factory=session_factory, redis=redis)  # type: ignore[arg-type]
    _app.state.vol_service = vol_svc

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
        # Phase 10b.2: snapshot writer mirrors NLV updates to
        # account_balance_snapshots and publishes portfolio.rollup.dirty for
        # the WS gateway to debounce + republish.
        balance_snapshot_writer = BalanceSnapshotWriter(redis=redis)
        _app.state.balance_snapshot_writer = balance_snapshot_writer
        broker_discoverer = BrokerDiscoverer(
            broker_registry,
            session_factory,
            redis=redis,
            balance_snapshot_writer=balance_snapshot_writer,
        )
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

        async def _oco_cancel_callable(broker_id: str, account_id: str, order_id: str) -> bool:
            """Cancel a broker order for an OCO sibling via the registry cancel path.

            Resolves account_id → (gateway_label, account_number) from the DB,
            then calls BrokerSidecarClient.cancel_order.
            """
            from sqlalchemy import text as _text

            async with session_factory() as _session:
                _result = await _session.execute(
                    _text(
                        "SELECT gateway_label, account_number FROM broker_accounts"
                        " WHERE id = :aid AND deleted_at IS NULL"
                    ),
                    {"aid": account_id},
                )
                _row = _result.mappings().one_or_none()
            if _row is None:
                log.warning("oco_cancel.account_not_found", account_id=account_id)
                return False
            _client = await broker_registry.get_client(str(_row["gateway_label"]))
            return await _client.cancel_order(str(_row["account_number"]), order_id)

        oco_orchestrator = OcoOrchestratorImpl(
            db=session_factory,
            redis=redis,
            cancel_callable=_oco_cancel_callable,
        )
        await oco_orchestrator.start()
        log.info("broker_lifespan_started")
    except MissingBrokerSecrets as exc:
        log.warning("broker_lifespan_skipped", reason=str(exc))

    # ── CRIT-1 fix: wire QuoteEngine into app.state ───────────────────────────
    # Engine is built regardless of whether the broker layer started — it only
    # needs the mTLS certs (shared with the broker sidecars). If the secrets
    # aren't provisioned yet, build_quote_engine returns None and WS connections
    # will close 1011 with a structured log (same observable behaviour as before,
    # but now documented and intentional rather than a silent None dereference).
    quote_engine = None
    try:
        quote_engine = await build_quote_engine(
            svc=svc,
            redis=redis,
            db_factory=session_factory,
        )
        if quote_engine is not None:
            await quote_engine.start()
            log.info("quote_engine_started", sources=sorted(quote_engine.streams.keys()))
        else:
            log.warning("quote_engine_skipped_not_configured")
    except Exception as exc:
        log.warning("quote_engine_start_failed", error=str(exc))
        quote_engine = None
    _app.state.quote_engine = quote_engine

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
    # ── Phase 11b chunk-B-close: alerts evaluator + delivery dispatcher ──────
    # Spec §6 wiring: AlertsEvaluator + bars_1m Redis subscriber + delivery
    # dispatcher + capability-flip pubsub listener + nightly retention sweep.
    try:
        async with session_factory() as alert_seed_db:
            await ensure_alert_capabilities_seeded(alert_seed_db)
    except Exception:
        log.exception("alerts.capability_seed_failed")

    alerts_evaluator: AlertsEvaluator | None = None
    alerts_dispatcher: DeliveryDispatcher | None = None
    alerts_bars_subscriber: AlertsBarsRedisSubscriber | None = None
    alerts_capability_listener: asyncio.Task[None] | None = None
    try:
        alerts_evaluator = AlertsEvaluator()
        # Codex chunk-B-close HIGH-1 fix: in-memory ``inst_id -> symbol``
        # cache, repopulated by every index rebuild. Replaces the previous
        # run_coroutine_threadsafe deadlock where the sync resolver tried to
        # schedule onto the same loop it was blocking with future.result.
        symbol_cache = SymbolCache()
        rebuild_index = build_index_rebuild_callback(
            session_factory=session_factory,
            evaluator=alerts_evaluator,
            symbol_cache=symbol_cache,
        )
        alerts_evaluator._rebuild_fn = rebuild_index
        # Codex chunk-B-close MED-2 fix: await the initial rebuild BEFORE
        # the worker + subscriber come online so the first bars_1m NOTIFY
        # hits a populated inverted index.
        await rebuild_index()
        await alerts_evaluator.start()

        # InApp channel is the only universally-available channel; webhook +
        # telegram channels need per-rule config that's wired in 11c.
        # redis-py Redis structurally satisfies the InAppChannel _RedisLike Protocol.
        in_app_channel = InAppChannel(redis=redis)  # type: ignore[arg-type]
        alerts_dispatcher = DeliveryDispatcher(channels={"in_app": in_app_channel})
        _app.state.alerts_evaluator = alerts_evaluator
        _app.state.alerts_dispatcher = alerts_dispatcher
        _app.state.alerts_symbol_cache = symbol_cache

        alerts_evaluator.start_worker(
            process=build_process_callback(
                session_factory=session_factory,
                dispatcher=alerts_dispatcher,
                evaluator=alerts_evaluator,
            )
        )

        alerts_bars_subscriber = AlertsBarsRedisSubscriber(
            redis=redis,
            evaluator=alerts_evaluator,
            # Pure-dict lookup — no loop hop, no blocking wait.
            resolve_symbol=symbol_cache.resolve,
        )
        alerts_bars_subscriber.start()

        async def _capability_invalidate() -> None:
            await rebuild_index()

        alerts_capability_listener = asyncio.create_task(
            run_capability_invalidation_listener(redis, on_invalidate=_capability_invalidate)
        )

        # Nightly retention sweep — apscheduler job at 03:30 UTC.
        async def _run_alert_retention_sweep() -> None:
            try:
                async with session_factory() as db:
                    count = await sweep_alert_fire_context(db)
                log.info("alerts.retention_sweep", deleted=count)
            except Exception:
                log.exception("alerts.retention_sweep_failed")

        scheduler.add_job(
            _run_alert_retention_sweep,
            CronTrigger(hour=3, minute=30, timezone="UTC"),
            id="alerts_retention_sweep",
            replace_existing=True,
        )

        async def _run_mute_expiry_restore() -> None:
            try:
                async with session_factory() as db:
                    result = await db.execute(
                        text(
                            "UPDATE alerts SET status='active', muted_until=NULL,"
                            " updated_at=now() WHERE status='disabled'"
                            " AND muted_until IS NOT NULL AND muted_until <= now()"
                            " RETURNING id"
                        )
                    )
                    restored = result.fetchall()
                    await db.commit()
                    if restored:
                        log.info("telegram.mute_expiry_restored", count=len(restored))
            except Exception:
                log.exception("telegram.mute_expiry_restore_failed")

        scheduler.add_job(
            _run_mute_expiry_restore,
            "interval",
            seconds=60,
            id="telegram_mute_expiry",
            replace_existing=True,
        )

        log.info("alerts.lifespan_started")
    except Exception:
        log.exception("alerts.lifespan_init_failed")

    telegram_allowlist = TelegramAllowlistService(config=svc)
    await telegram_allowlist.refresh()
    _app.state.telegram_allowlist = telegram_allowlist
    telegram_bot_instance = None
    tg_allowlist_listener: asyncio.Task[None] | None = None
    tg_config_listener: asyncio.Task[None] | None = None

    async def _watch_telegram_config_secret() -> None:
        pubsub = redis.pubsub()
        channel = "app_config:invalidate:telegram_config"
        while True:
            try:
                await pubsub.subscribe(channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        await asyncio.sleep(0)
                        continue
                    new_secret = await svc.reveal_secret("telegram", "webhook_secret")
                    if new_secret:
                        _app.state.telegram_webhook_secret = str(new_secret)
                        log.info("telegram.webhook_secret_rotated")
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                await pubsub.unsubscribe(channel)
                raise
            except Exception:
                log.exception("telegram.config_listener_crashed", retry_in=5.0)
                await pubsub.unsubscribe(channel)
                await asyncio.sleep(5.0)

    try:
        bot_token = await svc.reveal_secret("telegram", "bot_token")
        webhook_secret = await svc.reveal_secret("telegram", "webhook_secret")
        public_base_url = await svc.get("telegram", "public_base_url", "")
        public_base_url = str(public_base_url or "")
        if bot_token:
            webhook_url = f"{public_base_url}/api/telegram/webhook"
            telegram_bot_instance, webhook_ok = await telegram_startup(
                bot_token=str(bot_token),
                webhook_secret=str(webhook_secret or ""),
                webhook_url=webhook_url,
            )
            _app.state.telegram_bot = telegram_bot_instance
            _app.state.telegram_webhook_secret = str(webhook_secret or "")
            _app.state.telegram_webhook_status = "set" if webhook_ok else "webhook_failed"
            tg_rate_limiter = TelegramRateLimiter(redis=redis)
            tg_dispatcher = build_dispatcher()
            chat_id_hash_salt = (
                await svc.reveal_secret("telegram", "chat_id_hash_salt") or "default-salt"
            )
            tg_chat = TelegramChat(
                ai_client=getattr(_app.state, "ai_router", None),
                redis=redis,
                chat_id_hash_salt=str(chat_id_hash_salt),
            )
            register_tg_handlers(
                tg_dispatcher,
                allowlist=telegram_allowlist,
                rate_limiter=tg_rate_limiter,
                db_factory=session_factory,
                redis=redis,
                request_app=_app,
                tg_chat=tg_chat,
            )
            _telegram_api_module.dp = tg_dispatcher
            tg_allowlist_listener = asyncio.create_task(
                telegram_allowlist.run_pubsub_listener(redis)
            )
            tg_config_listener = asyncio.create_task(_watch_telegram_config_secret())
            tg_allowlist_listener.add_done_callback(
                lambda t: (
                    not t.cancelled()
                    and t.exception() is not None
                    and log.error(
                        "telegram.allowlist_listener_died",
                        error_class=type(t.exception()).__name__,
                    )
                )
            )
        else:
            _app.state.telegram_bot = None
            _app.state.telegram_webhook_secret = ""
            _app.state.telegram_webhook_status = "failed"
            log.info("telegram.bot_token_not_seeded")
    except Exception:
        log.exception("telegram.lifespan_init_failed")
        _app.state.telegram_bot = None
        _app.state.telegram_webhook_secret = ""
        _app.state.telegram_webhook_status = "failed"
        public_base_url = ""

    tg_channel = TelegramDeliveryChannel(
        bot=_app.state.telegram_bot,
        allowlist=telegram_allowlist,
        public_base_url=public_base_url,
    )
    if alerts_dispatcher is not None:
        alerts_dispatcher._channels["telegram"] = tg_channel

    scheduler.start()
    _app.state.scheduler = scheduler

    # Run once immediately on startup (non-blocking).
    pre_warm_task: asyncio.Task[None] = asyncio.create_task(_run_pre_warm())

    # HIGH-code-2: populate Schwab token-age + uses-per-24h gauges every 5 min.
    schwab_metrics_task: asyncio.Task[None] = asyncio.create_task(
        _update_schwab_token_metrics(redis, session_factory)
    )

    log.info("startup_ok", env=settings.env)
    try:
        yield
    finally:
        # Phase 11b chunk-B-close: drain alerts evaluator + subscribers before
        # broker/redis shutdown so the worker doesn't try to write to a closed
        # session.
        if alerts_capability_listener is not None:
            alerts_capability_listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await alerts_capability_listener
        if alerts_bars_subscriber is not None:
            await alerts_bars_subscriber.stop()
        if alerts_evaluator is not None:
            await alerts_evaluator.stop()
        if tg_allowlist_listener is not None:
            tg_allowlist_listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tg_allowlist_listener
        if tg_config_listener is not None:
            tg_config_listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tg_config_listener
        if telegram_bot_instance is not None:
            await telegram_shutdown(telegram_bot_instance)
        orphan_sweeper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await orphan_sweeper_task
        # Cancel the initial pre-warm task if still running (codex-default B).
        pre_warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pre_warm_task
        schwab_metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await schwab_metrics_task
        scheduler.shutdown(wait=False)
        # ── CRIT-1: stop QuoteEngine before broker/redis shutdown ─────────────
        if quote_engine is not None:
            try:
                await quote_engine.stop()
            except Exception:
                log.exception("quote_engine_stop_failed")
        _app.state.quote_engine = None
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
        # Phase 10b.2: defensive — drain publish tasks ONLY when the
        # discoverer didn't get instantiated (partial-init failure path).
        # When broker_discoverer.stop() runs, it already calls writer.stop().
        # Double-calling stop() is racy (the discard done_callback can run
        # asynchronously between calls, leaking pending entries — review
        # HIGH #3).
        elif getattr(_app.state, "balance_snapshot_writer", None) is not None:
            await _app.state.balance_snapshot_writer.stop()
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
        listener_capability.cancel()
        listener_ai_secrets.cancel()
        for t in (listener_config, listener_secrets, listener_capability, listener_ai_secrets):
            try:
                await t
            except asyncio.CancelledError:
                pass
        try:
            await callback_server.stop(grace=5)
        except Exception:
            log.exception("callback_server_stop_failed")
        try:
            await _app.state.ai_cost_ledger.stop()
        except Exception:
            log.exception("ai_cost_ledger_stop_failed")
        try:
            await _app.state.ollama_health_watcher.stop()
        except Exception:
            log.exception("ollama_health_watcher_stop_failed")
        try:
            await redis.aclose()
        except Exception:
            log.exception("redis_aclose_failed")
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
app.include_router(admin_risk_router)
app.include_router(risk_router)
app.include_router(sizing_router)
app.include_router(portfolio_router)
app.include_router(brokers_admin_router)
app.include_router(accounts_router)
app.include_router(ai_router)
app.include_router(alerts_router)
app.include_router(brokers_router)
app.include_router(capabilities_router)
app.include_router(metrics_router)
app.include_router(orders_router)
app.include_router(fills_router)
app.include_router(contracts_router)
app.include_router(oauth_router)
app.include_router(sse_router)
app.include_router(admin_metrics_router)
app.include_router(admin_alerts_router)
app.include_router(admin_telegram_router)
app.include_router(telegram_router)
app.include_router(ws_quotes_router)
app.include_router(ws_portfolio_router)
app.include_router(ws_ai_router)
app.include_router(ws_alerts_router)
app.include_router(chart_layouts_router)
app.include_router(bars_router)  # Task 28: GET /api/bars cursor pagination
app.include_router(bars_ws_router)  # Task 31: WS /ws/bars live-tail


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = "ok"
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        db_ok = "unreachable"
    return {"status": "ok", "env": settings.env, "db": db_ok}
