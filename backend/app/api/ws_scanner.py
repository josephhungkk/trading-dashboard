from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = structlog.get_logger()
router = APIRouter()

_HEARTBEAT_INTERVAL = 30
_SEND_TIMEOUT = 2.0


@router.websocket("/ws/scanner/runs/{scan_id}")
async def ws_scanner_run(websocket: WebSocket, scan_id: str) -> None:
    origin = websocket.headers.get("origin", "")
    if origin and not origin.startswith("https://"):
        await websocket.close(code=4003)
        return
    await websocket.accept()
    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    channel = f"scanner:run:{scan_id}"
    await pubsub.subscribe(channel)
    recv_task = asyncio.create_task(_drain_recv(websocket))
    try:
        last_hb = asyncio.get_event_loop().time()
        while True:
            now = asyncio.get_event_loop().time()
            if now - last_hb >= _HEARTBEAT_INTERVAL:
                hb = json.dumps(
                    {
                        "v": 1,
                        "type": "heartbeat",
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
                try:
                    await asyncio.wait_for(websocket.send_text(hb), timeout=_SEND_TIMEOUT)
                except Exception:
                    break
                last_hb = now
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg and msg["type"] == "message":
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    await asyncio.wait_for(
                        websocket.send_text(data),
                        timeout=_SEND_TIMEOUT,
                    )
                except Exception:
                    break
            if recv_task.done():
                break
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        await pubsub.unsubscribe(channel)
        await websocket.close()


async def _drain_recv(ws: WebSocket) -> None:
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
