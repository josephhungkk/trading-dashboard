"""Phase 10b.2 §6 — /ws/portfolio/rollup gateway.

Architecture invariants (architect review applied inline):
  - CSWSH origin check before auth (HIGH #2)
  - 1008 close code on origin/capacity/auth miss (HIGH #2)
  - listen() pattern, not get_message polling (HIGH #3)
  - 250ms per-conn compute cache + 500ms debounce (HIGH #3)
  - asyncio.wait_for on every send (HIGH #3)
  - Frame schema {"version": 1, ...} (MED #4)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState

from app.api.ws_auth import require_admin_jwt_ws
from app.core import metrics
from app.core.db import SessionLocal
from app.services.common.ws_envelope import WSEnvelopeConfig, make_ws_endpoint
from app.services.orders_service import PreviewUnavailable
from app.services.portfolio_rate_limiter import (
    PortfolioRateLimitExceededError,
    get_portfolio_limiter,
)
from app.services.portfolio_rollup_service import PortfolioRollupService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["portfolio-ws"])

_DIRTY_CHANNEL = "portfolio.rollup.dirty"
_COMPUTE_CACHE_TTL_S = 0.25
_DEBOUNCE_S = 0.5
_SEND_TIMEOUT_S = 2.0
_HEARTBEAT_S = 30.0
_MAX_WS_CONNECTIONS = 20

_active_connections = 0


@router.websocket("/ws/portfolio/rollup")
async def ws_portfolio_rollup(
    ws: WebSocket,
    base: str = Query(default="GBP", pattern=r"^[A-Z]{3}$", max_length=3),
) -> None:
    global _active_connections

    from app.core.config import settings as _settings  # local import avoids circular

    state_origins = getattr(ws.app.state, "cors_origins", None)
    allowed_origins = frozenset(
        state_origins if state_origins is not None else _settings.cors_origins
    )
    cfg = WSEnvelopeConfig(
        allowed_origins=allowed_origins,
        max_connections=_MAX_WS_CONNECTIONS,
        active_counter=lambda: _active_connections,
        send_timeout_s=_SEND_TIMEOUT_S,
        heartbeat_s=_HEARTBEAT_S,
    )
    env = make_ws_endpoint(ws, cfg)
    accepted = await env.handshake(auth=require_admin_jwt_ws)
    if not accepted:
        return
    assert env.jwt_subject is not None
    jwt_subject: str = env.jwt_subject

    # 3a. Rate limit (final-reviewer HIGH #1) — share the bucket with the 3
    # REST endpoints. The initial-snapshot compute is identical work to
    # GET /api/portfolio/rollup, so an unauthenticated WS storm would bypass
    # the REST limiter. evict_stale runs after a successful check, mirroring
    # the REST helper at portfolio.py:_check_rate_limit.
    limiter = get_portfolio_limiter()
    try:
        limiter.check(jwt_subject)
    except PortfolioRateLimitExceededError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="rate_limited")
        return
    limiter.evict_stale(jwt_subject)

    _active_connections += 1
    metrics.portfolio_rollup_ws_connections.set(_active_connections)

    redis = ws.app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe(_DIRTY_CHANNEL)
    dirty = asyncio.Event()

    listener_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None

    async def _listen() -> None:
        async for _msg in pubsub.listen():
            dirty.set()

    async def _heartbeat() -> None:
        # Loop condition relies on exception guards + outer-finally cancel.
        # `ws.client_state == CONNECTED` is intentionally NOT used here:
        # the check is a snapshot that races with the recv_drain → close
        # transition. The TimeoutError / WebSocketDisconnect handlers below
        # are the actual exit criterion (reviewer HIGH).
        while not env.disconnected.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            try:
                async with SessionLocal() as session:
                    rollup = await PortfolioRollupService(session, redis).compute_live(base)
            except PreviewUnavailable:
                continue
            stale_ids = [str(uid) for uid in rollup.stale_accounts]
            if not await env.send_or_close(
                {"version": 1, "type": "stale", "account_ids": stale_ids}
            ):
                return

    try:
        listener_task = asyncio.create_task(_listen())
        heartbeat_task = asyncio.create_task(_heartbeat())
        env.start_recv_drain()

        # Initial snapshot
        last_payload: dict[str, Any] | None = None
        last_compute = 0.0
        last_send = 0.0
        try:
            async with SessionLocal() as session:
                initial = await PortfolioRollupService(session, redis).compute_live(base)
            last_payload = initial.model_dump(mode="json")
            last_compute = time.monotonic()
            if not await env.send_or_close(
                {"version": 1, "type": "snapshot", "payload": last_payload}
            ):
                metrics.portfolio_rollup_ws_send_timeout_total.inc()
                return
            metrics.portfolio_rollup_ws_publish_total.inc()
            last_send = time.monotonic()
        except PreviewUnavailable:
            log.warning("portfolio_ws_initial_skip_preview_unavailable")

        # Main push loop.
        # Reviewer MED: don't spawn 2 short-lived tasks per iteration. Use
        # asyncio.wait_for(dirty.wait()) for the debounce window and check
        # disconnected.is_set() immediately — recv_drain sets BOTH events on
        # disconnect (we add a `dirty.set()` there) so the wait wakes promptly.
        while ws.client_state == WebSocketState.CONNECTED and not env.disconnected.is_set():
            try:
                await asyncio.wait_for(dirty.wait(), timeout=_DEBOUNCE_S)
            except TimeoutError:
                pass
            if env.disconnected.is_set():
                break
            dirty.clear()

            now = time.monotonic()
            if (now - last_send) < _DEBOUNCE_S:
                continue

            if (now - last_compute) < _COMPUTE_CACHE_TTL_S and last_payload is not None:
                payload_dict = last_payload
            else:
                try:
                    async with SessionLocal() as session:
                        fresh = await PortfolioRollupService(session, redis).compute_live(base)
                except PreviewUnavailable:
                    log.warning("portfolio_ws_skip_preview_unavailable")
                    continue
                payload_dict = fresh.model_dump(mode="json")
                last_payload = payload_dict
                last_compute = now

            if not await env.send_or_close(
                {"version": 1, "type": "snapshot", "payload": payload_dict}
            ):
                metrics.portfolio_rollup_ws_send_timeout_total.inc()
                return
            metrics.portfolio_rollup_ws_publish_total.inc()
            last_send = now

    except WebSocketDisconnect:
        log.info("portfolio_ws_disconnect")
    except Exception:
        log.exception("portfolio_ws_unhandled")
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="unhandled")
    finally:
        for t in (listener_task, heartbeat_task):
            if t is not None:
                t.cancel()
        await asyncio.gather(
            *(t for t in (listener_task, heartbeat_task) if t is not None),
            return_exceptions=True,
        )
        await env.cleanup()
        try:
            await pubsub.unsubscribe(_DIRTY_CHANNEL)
        except Exception:
            log.exception("portfolio_ws_unsubscribe_failed")
        _active_connections -= 1
        metrics.portfolio_rollup_ws_connections.set(_active_connections)
