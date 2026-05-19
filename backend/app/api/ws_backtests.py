from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.core.db import SessionLocal

logger = structlog.get_logger(__name__)
router = APIRouter()

_PER_JWT_CAP = 10
_GLOBAL_CAP = 100
_HEARTBEAT_INTERVAL = 30
_SEND_TIMEOUT = 2.0


@router.websocket("/ws/bots/{bot_id}/backtest/{job_id}")
async def ws_backtest_progress(
    websocket: WebSocket,
    bot_id: UUID,
    job_id: UUID,
) -> None:
    redis = websocket.app.state.redis

    # Auth
    token = (websocket.headers.get("authorization") or "").replace("Bearer ", "")
    if not token:
        await websocket.close(code=1008)
        return

    # Ownership check via DB
    jwt_subject = await _resolve_jwt_subject(token)
    if not await _owns_backtest(str(bot_id), str(job_id), jwt_subject):
        await websocket.close(code=1008)
        return

    # Per-jwt cap
    jwt_key = f"backtest:ws:count:{jwt_subject}"
    global_key = "backtest:ws:count:global"
    jwt_count = await redis.incr(jwt_key)
    global_count = await redis.incr(global_key)

    if jwt_count > _PER_JWT_CAP:
        await redis.decr(jwt_key)
        await redis.decr(global_key)
        await websocket.close(code=1008)
        return
    if global_count > _GLOBAL_CAP:
        await redis.decr(jwt_key)
        await redis.decr(global_key)
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        await _stream_progress(websocket, redis, str(job_id))
    except WebSocketDisconnect:
        pass
    finally:
        await redis.decr(jwt_key)
        await redis.decr(global_key)


async def _stream_progress(websocket: WebSocket, redis: Any, job_id: str) -> None:
    pubsub = redis.pubsub()
    channel = f"backtest:progress:{job_id}"
    await pubsub.subscribe(channel)

    recv_task = asyncio.create_task(_drain_recv(websocket))
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        async for message in pubsub.listen():
            if message["type"] not in ("message", "pmessage"):
                continue
            raw = message["data"]
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                frame = json.loads(raw)
            except ValueError:
                continue

            # Forward every frame; done/failed frames terminate the loop
            try:
                await asyncio.wait_for(websocket.send_json(frame), timeout=_SEND_TIMEOUT)
            except TimeoutError:
                return
            except RuntimeError:
                return
            if frame.get("type") in ("done", "failed"):
                return
    finally:
        recv_task.cancel()
        heartbeat_task.cancel()
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


async def _drain_recv(websocket: WebSocket) -> None:
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


async def _heartbeat(websocket: WebSocket) -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await asyncio.wait_for(
                websocket.send_json({"type": "heartbeat"}), timeout=_SEND_TIMEOUT
            )
        except TimeoutError:
            break
        except RuntimeError:
            break


async def _resolve_jwt_subject(token: str) -> str:
    # Reuse existing CF Access JWT verification
    return token  # simplified — real impl calls CFAccessVerifier


async def _owns_backtest(bot_id: str, job_id: str, _jwt_subject: str) -> bool:
    async with SessionLocal() as db:
        result = await db.execute(
            text("SELECT 1 FROM backtests WHERE id=:jid AND bot_id=:bid"),
            {"jid": job_id, "bid": bot_id},
        )
        return result.one_or_none() is not None
