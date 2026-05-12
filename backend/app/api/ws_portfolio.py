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
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.websockets import WebSocketState

from app.api.ws_auth import require_admin_jwt_ws
from app.core import metrics
from app.core.db import SessionLocal
from app.services.orders_service import PreviewUnavailable
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


def _allowed_origin(ws: WebSocket, allowed: list[str]) -> bool:
    """CSWSH protection — mirrors ws_quotes._allowed_origin.

    Empty Origin permitted only when peer is the WG dev gateway (10.10.0.1),
    which is a trusted non-browser network path.
    """
    origin = ws.headers.get("origin", "")
    if not origin:
        client_host = ws.client.host if ws.client else ""
        return client_host == "10.10.0.1"
    return origin in allowed


@router.websocket("/ws/portfolio/rollup")
async def ws_portfolio_rollup(ws: WebSocket, base: str = Query(default="GBP")) -> None:
    global _active_connections

    # 1. Pre-accept connection cap
    if _active_connections >= _MAX_WS_CONNECTIONS:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="capacity")
        return

    # 2. CSWSH origin check (prefer app.state, fall back to settings)
    from app.core.config import settings as _settings  # local import avoids circular

    state_origins = getattr(ws.app.state, "cors_origins", None)
    allowed_origins = list(state_origins if state_origins is not None else _settings.cors_origins)
    if not _allowed_origin(ws, allowed_origins):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin")
        return

    # 3. Auth — helper raises WebSocketException on miss
    try:
        await require_admin_jwt_ws(ws)
    except WebSocketException:
        return

    # 4. Accept + bookkeeping
    await ws.accept()
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
        while ws.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(_HEARTBEAT_S)
            try:
                async with SessionLocal() as session:
                    rollup = await PortfolioRollupService(session, redis).compute_live(base)
            except PreviewUnavailable:
                continue
            stale_ids = [str(uid) for uid in rollup.stale_accounts]
            try:
                await asyncio.wait_for(
                    ws.send_json({"version": 1, "type": "stale", "account_ids": stale_ids}),
                    timeout=_SEND_TIMEOUT_S,
                )
            except TimeoutError:
                return
            except WebSocketDisconnect:
                return

    try:
        listener_task = asyncio.create_task(_listen())
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Initial snapshot
        last_payload: dict[str, Any] | None = None
        last_compute = 0.0
        last_send = 0.0
        try:
            async with SessionLocal() as session:
                initial = await PortfolioRollupService(session, redis).compute_live(base)
            last_payload = initial.model_dump(mode="json")
            last_compute = time.monotonic()
            await asyncio.wait_for(
                ws.send_json({"version": 1, "type": "snapshot", "payload": last_payload}),
                timeout=_SEND_TIMEOUT_S,
            )
            metrics.portfolio_rollup_ws_publish_total.inc()
            last_send = time.monotonic()
        except PreviewUnavailable:
            log.warning("portfolio_ws_initial_skip_preview_unavailable")
        except TimeoutError:
            metrics.portfolio_rollup_ws_send_timeout_total.inc()
            await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="send-timeout")
            return

        # Main push loop
        while ws.client_state == WebSocketState.CONNECTED:
            try:
                await asyncio.wait_for(dirty.wait(), timeout=_DEBOUNCE_S)
            except TimeoutError:
                pass
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

            try:
                await asyncio.wait_for(
                    ws.send_json({"version": 1, "type": "snapshot", "payload": payload_dict}),
                    timeout=_SEND_TIMEOUT_S,
                )
            except TimeoutError:
                metrics.portfolio_rollup_ws_send_timeout_total.inc()
                await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="send-timeout")
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
        if listener_task is not None:
            listener_task.cancel()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        await asyncio.gather(
            *(t for t in (listener_task, heartbeat_task) if t is not None),
            return_exceptions=True,
        )
        try:
            await pubsub.unsubscribe(_DIRTY_CHANNEL)
        except Exception:
            log.exception("portfolio_ws_unsubscribe_failed")
        _active_connections -= 1
        metrics.portfolio_rollup_ws_connections.set(_active_connections)
