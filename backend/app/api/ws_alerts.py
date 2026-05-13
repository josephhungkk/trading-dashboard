"""Phase 11b-C4: WS /ws/alerts/feed for the alerts engine."""

from __future__ import annotations

import asyncio
import contextlib
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState
from redis.exceptions import RedisError

from app.api.ws_auth import require_admin_jwt_ws
from app.services.common.ws_envelope import WSEnvelopeConfig, make_ws_endpoint

log = structlog.get_logger(__name__)
router = APIRouter(tags=["alerts-ws"])

_MAX_WS_CONNECTIONS = 20
_HEARTBEAT_S = 30.0
_SEND_TIMEOUT_S = 2.0
_PUBSUB_POLL_S = 1.0

_active_feed_connections = 0


def _allowed_origins(ws: WebSocket) -> frozenset[str]:
    from app.core.config import settings as _settings

    state_origins = getattr(ws.app.state, "cors_origins", None)
    return frozenset(state_origins if state_origins is not None else _settings.cors_origins)


@router.websocket("/ws/alerts/feed")
async def ws_alerts_feed(ws: WebSocket) -> None:
    global _active_feed_connections

    cfg = WSEnvelopeConfig(
        allowed_origins=_allowed_origins(ws),
        max_connections=_MAX_WS_CONNECTIONS,
        active_counter=lambda: _active_feed_connections,
        send_timeout_s=_SEND_TIMEOUT_S,
        heartbeat_s=_HEARTBEAT_S,
    )
    env = make_ws_endpoint(ws, cfg)
    accepted = await env.handshake(auth=require_admin_jwt_ws)
    if not accepted:
        return
    assert env.jwt_subject is not None

    _active_feed_connections += 1
    redis = ws.app.state.redis
    pubsub = redis.pubsub()
    channel = f"alerts:fire:{env.jwt_subject}"
    heartbeat_task: asyncio.Task[None] | None = None
    subscribed = False

    async def _heartbeat() -> None:
        while not env.disconnected.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            if not await env.send_or_close({"v": 1, "type": "heartbeat"}):
                return

    try:
        await pubsub.subscribe(channel)
        subscribed = True
        env.start_recv_drain()
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Codex chunk-C HIGH — pubsub.listen() blocks on Redis indefinitely
        # when no messages arrive, leaking the subscription + connection slot
        # after the client disconnects. Poll with a bounded timeout so the
        # env.disconnected.is_set() check actually runs.
        while not env.disconnected.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_PUBSUB_POLL_S)
            if msg is None:
                continue
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                payload = json.loads(data)
            except TypeError:
                continue
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if not await env.send_or_close({"v": 1, "type": "fire", **payload}):
                return
    except WebSocketDisconnect:
        log.info("ws_alerts_feed_disconnect")
    except Exception:
        log.exception("ws_alerts_feed_unhandled")
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="unhandled")
    finally:
        _active_feed_connections -= 1
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await env.cleanup()
        if subscribed:
            with contextlib.suppress(ConnectionError, RedisError):
                await pubsub.unsubscribe(channel)
        with contextlib.suppress(Exception):
            await pubsub.aclose()
