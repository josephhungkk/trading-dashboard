from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_auth import require_admin_jwt_ws

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
        await pubsub.aclose()
        logger.info("ws_bots_status_disconnected", total=len(_active))


@router.websocket("/ws/bots/{bot_id}/advisor")
async def ws_bot_advisor(websocket: WebSocket, bot_id: str) -> None:
    if len(_active) >= _WS_CAP:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        await require_admin_jwt_ws(websocket)
    except Exception:
        return

    _active.add(websocket)
    channel = f"bot:advisor:decision:{bot_id}"
    logger.info("ws_bot_advisor_connected", bot_id=bot_id, total=len(_active))

    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    try:
        await _forward_pubsub_json(pubsub, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _active.discard(websocket)
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        logger.info("ws_bot_advisor_disconnected", bot_id=bot_id, total=len(_active))


@router.websocket("/ws/bots/advisor")
async def ws_bots_advisor_admin(websocket: WebSocket) -> None:
    if len(_active) >= _WS_CAP:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    try:
        await require_admin_jwt_ws(websocket)
    except Exception:
        return

    _active.add(websocket)
    pattern = "bot:advisor:decision:*"
    logger.info("ws_bots_advisor_admin_connected", total=len(_active))

    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    await pubsub.psubscribe(pattern)

    try:
        await _forward_pubsub_json(pubsub, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _active.discard(websocket)
        await pubsub.punsubscribe(pattern)
        await pubsub.aclose()
        logger.info("ws_bots_advisor_admin_disconnected", total=len(_active))


async def _forward_pubsub_json(pubsub: Any, websocket: WebSocket) -> None:
    async for message in pubsub.listen():
        if message["type"] not in ("pmessage", "message"):
            continue
        raw = message["data"]
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        try:
            await asyncio.wait_for(websocket.send_json(payload), timeout=2.0)
        except TimeoutError:
            return
        except Exception:
            return
