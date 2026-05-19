from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_auth import require_admin_jwt_ws
from app.core import metrics

log = structlog.get_logger()
router = APIRouter()

_HEARTBEAT_INTERVAL = 30
_SEND_TIMEOUT = 2.0
_MAX_WS_CONNECTIONS = 50

_active_connections = 0


@router.websocket("/ws/scanner/runs/{scan_id}")
async def ws_scanner_run(websocket: WebSocket, scan_id: str) -> None:
    global _active_connections

    from app.core.config import settings as _settings

    state_origins = getattr(websocket.app.state, "cors_origins", None)
    allowed_origins: frozenset[str] = frozenset(
        state_origins if state_origins is not None else _settings.cors_origins
    )
    origin = websocket.headers.get("origin", "")
    if origin and origin not in allowed_origins:
        await websocket.close(code=4003)
        return

    if _active_connections >= _MAX_WS_CONNECTIONS:
        await websocket.close(code=1008)
        return

    try:
        jwt_subject = await require_admin_jwt_ws(websocket)
    except Exception:
        return

    await websocket.accept()
    _active_connections += 1
    metrics.scanner_ws_connections.set(_active_connections)

    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    channel = f"scanner:run:{scan_id}"
    await pubsub.subscribe(channel)

    recv_task = asyncio.create_task(_drain_recv(websocket))
    listen_task = asyncio.create_task(_listen_pubsub(pubsub, websocket))
    hb_task = asyncio.create_task(_heartbeat(websocket))

    try:
        done, _ = await asyncio.wait(
            [recv_task, listen_task, hb_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        _ = done
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        listen_task.cancel()
        hb_task.cancel()
        await asyncio.gather(recv_task, listen_task, hb_task, return_exceptions=True)
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        _active_connections -= 1
        metrics.scanner_ws_connections.set(_active_connections)
        try:
            await websocket.close()
        except Exception:
            pass

    log.debug("ws_scanner.disconnected", scan_id=scan_id, jwt_subject=jwt_subject)


async def _listen_pubsub(pubsub: object, ws: WebSocket) -> None:
    try:
        async for msg in pubsub.listen():  # type: ignore[attr-defined]
            if msg["type"] != "message":
                continue
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode()
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=_SEND_TIMEOUT)
                metrics.scanner_ws_frames_sent_total.inc()
            except Exception:
                return
    except WebSocketDisconnect:
        pass


async def _heartbeat(ws: WebSocket) -> None:
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            hb = json.dumps(
                {
                    "v": 1,
                    "type": "heartbeat",
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
            try:
                await asyncio.wait_for(ws.send_text(hb), timeout=_SEND_TIMEOUT)
                metrics.scanner_ws_heartbeat_total.inc()
            except Exception:
                return
    except WebSocketDisconnect:
        pass


async def _drain_recv(ws: WebSocket) -> None:
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
