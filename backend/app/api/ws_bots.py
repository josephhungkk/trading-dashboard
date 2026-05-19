from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)
router = APIRouter()

_WS_CAP = 50
_CONFLATION_MS = 500
_active: set[WebSocket] = set()


@router.websocket("/ws/bots/status")
async def ws_bots_status(websocket: WebSocket) -> None:
    if len(_active) >= _WS_CAP:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _active.add(websocket)
    logger.info("ws_bots_status_connected", total=len(_active))

    redis = websocket.app.state.redis
    pending: dict[str, Any] = {}
    flush_task: asyncio.Task[None] | None = None
    pubsub = redis.pubsub()
    await pubsub.psubscribe("bot:status:*")

    try:
        async for message in pubsub.listen():
            if message["type"] not in ("pmessage", "message"):
                continue
            raw = message["data"]
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                frame = json.loads(raw)
            except Exception:
                continue

            bot_id = frame.get("bot_id", "unknown")
            pending[bot_id] = frame

            if flush_task is None or flush_task.done():

                async def flush() -> None:
                    await asyncio.sleep(_CONFLATION_MS / 1000)
                    if pending:
                        for f in list(pending.values()):
                            try:
                                await asyncio.wait_for(websocket.send_json(f), timeout=2.0)
                            except Exception:
                                pass
                        pending.clear()

                flush_task = asyncio.create_task(flush())

    except WebSocketDisconnect:
        pass
    finally:
        _active.discard(websocket)
        await pubsub.punsubscribe("bot:status:*")
        logger.info("ws_bots_status_disconnected", total=len(_active))
