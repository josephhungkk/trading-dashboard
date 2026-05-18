"""Phase 15b: WS /ws/crypto/book/{canonical_id} real-time order book stream."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState
from redis.exceptions import RedisError

from app.api.ws_auth import require_admin_jwt_ws
from app.core import metrics
from app.services.common.ws_envelope import WSEnvelopeConfig, make_ws_endpoint

log = structlog.get_logger(__name__)
router = APIRouter(tags=["crypto-ws"])

_MAX_CONNECTIONS = 50
_HEARTBEAT_S = 30.0
_SEND_TIMEOUT_S = 2.0
_MIN_SEND_INTERVAL = 0.5  # max 2 updates/s conflation

_active_connections = 0


def _allowed_origins(ws: WebSocket) -> frozenset[str]:
    from app.core.config import settings as _settings

    state_origins = getattr(ws.app.state, "cors_origins", None)
    return frozenset(state_origins if state_origins is not None else _settings.cors_origins)


@router.websocket("/ws/crypto/book/{canonical_id}")
async def ws_crypto_book(ws: WebSocket, canonical_id: str) -> None:
    global _active_connections

    cfg = WSEnvelopeConfig(
        allowed_origins=_allowed_origins(ws),
        max_connections=_MAX_CONNECTIONS,
        active_counter=lambda: _active_connections,
        send_timeout_s=_SEND_TIMEOUT_S,
        heartbeat_s=_HEARTBEAT_S,
    )
    env = make_ws_endpoint(ws, cfg)
    accepted = await env.handshake(auth=require_admin_jwt_ws)
    if not accepted:
        return

    _active_connections += 1
    metrics.ws_crypto_book_connections.inc()
    redis = ws.app.state.redis
    heartbeat_task: asyncio.Task[None] | None = None

    async def _send(payload: dict) -> bool:
        sent = await env.send_or_close(payload)
        if sent:
            metrics.ws_crypto_book_messages_total.labels(canonical_id=canonical_id).inc()
        return sent

    async def _heartbeat() -> None:
        while not env.disconnected.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            if not await _send({"type": "heartbeat"}):
                return

    try:
        # Send initial snapshot from Redis hash
        try:
            raw = await redis.hgetall(f"crypto:book:snap:{canonical_id}")
        except RedisError:
            raw = {}

        if raw and b"bids" in raw and b"asks" in raw:
            snapshot: dict = {
                "type": "book_snapshot",
                "canonical_id": canonical_id,
                "bids": json.loads(raw[b"bids"]),
                "asks": json.loads(raw[b"asks"]),
            }
        else:
            snapshot = {
                "type": "book_snapshot",
                "canonical_id": canonical_id,
                "bids": [],
                "asks": [],
            }

        if not await _send(snapshot):
            return

        env.start_recv_drain()
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Stream deltas from Redis stream, conflated at 2/s
        last_id: bytes = b"$"
        last_send = 0.0
        pending_deltas: list[dict] = []

        while not env.disconnected.is_set():
            try:
                entries = await redis.xread(
                    {f"crypto:book:{canonical_id}": last_id},
                    block=500,
                    count=100,
                )
            except RedisError as exc:
                log.warning("crypto_book_xread_error", canonical_id=canonical_id, error=str(exc))
                await asyncio.sleep(1)
                continue

            if entries:
                for _stream, messages in entries:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        pending_deltas.append(
                            {
                                "side": fields.get(b"side", b"").decode(),
                                "price": fields.get(b"price", b"").decode(),
                                "qty": fields.get(b"qty", b"").decode(),
                                "seq": int(fields.get(b"seq", b"0")),
                            }
                        )

            now = time.monotonic()
            if pending_deltas and now - last_send >= _MIN_SEND_INTERVAL:
                frame = {
                    "type": "book_deltas",
                    "canonical_id": canonical_id,
                    "deltas": pending_deltas,
                }
                if not await _send(frame):
                    return
                pending_deltas = []
                last_send = now

    except WebSocketDisconnect:
        log.info("ws_crypto_book_disconnect", canonical_id=canonical_id)
    except Exception:
        log.exception("ws_crypto_book_unhandled", canonical_id=canonical_id)
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="unhandled")
    finally:
        _active_connections -= 1
        metrics.ws_crypto_book_connections.dec()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await env.cleanup()
