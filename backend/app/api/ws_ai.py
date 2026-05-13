"""Phase 11a-C: WS endpoints for AI router."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from uuid import uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from app.api.ws_auth import require_admin_jwt_ws
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
)
from app.services.ai.types import CompletionRequest
from app.services.common.ws_envelope import WSEnvelopeConfig, make_ws_endpoint

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ai-ws"])

_MAX_WS_CONNECTIONS = 10
_HEARTBEAT_S = 30.0
_SEND_TIMEOUT_S = 10.0
_TURNS_PER_MIN = 5
_TURN_WINDOW_S = 60

_active_chat_connections = 0


def _allowed_origins(ws: WebSocket) -> frozenset[str]:
    from app.core.config import settings as _settings

    state_origins = getattr(ws.app.state, "cors_origins", None)
    return frozenset(state_origins if state_origins is not None else _settings.cors_origins)


@router.websocket("/ws/ai/chat")
async def ws_ai_chat(ws: WebSocket) -> None:
    global _active_chat_connections

    cfg = WSEnvelopeConfig(
        allowed_origins=_allowed_origins(ws),
        max_connections=_MAX_WS_CONNECTIONS,
        active_counter=lambda: _active_chat_connections,
        send_timeout_s=_SEND_TIMEOUT_S,
        heartbeat_s=_HEARTBEAT_S,
    )
    env = make_ws_endpoint(ws, cfg)
    accepted = await env.handshake(auth=require_admin_jwt_ws)
    if not accepted:
        return
    assert env.jwt_subject is not None

    _active_chat_connections += 1
    turn_timestamps: deque[float] = deque()
    stream_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None

    async def _heartbeat() -> None:
        while not env.disconnected.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            if not await env.send_or_close({"version": 1, "type": "heartbeat"}):
                return

    async def _run_stream(req: CompletionRequest) -> None:
        request_id = uuid4()
        try:
            async for chunk in ws.app.state.ai_router.stream(req, jwt_subject=env.jwt_subject):
                chunk_request_id = getattr(chunk, "request_id", request_id)
                request_id = chunk_request_id
                if not await env.send_or_close(
                    {
                        "version": 1,
                        "type": "chunk",
                        "text": getattr(chunk, "text", getattr(chunk, "delta", "")),
                        "request_id": str(chunk_request_id),
                    }
                ):
                    return
            await env.send_or_close({"version": 1, "type": "done", "request_id": str(request_id)})
        except (
            LocalModelsUnavailableError,
            AIProxyUnavailableError,
            AIToolCallingNotSupportedError,
        ) as exc:
            message = (
                "tool calling not supported"
                if isinstance(exc, AIToolCallingNotSupportedError)
                else "model unavailable"
            )
            await env.send_or_close(
                {
                    "version": 1,
                    "type": "error",
                    "error_class": type(exc).__name__,
                    "message": message,
                }
            )
        except Exception:
            log.exception("ws_ai_chat_unhandled")
            await env.send_or_close(
                {
                    "version": 1,
                    "type": "error",
                    "error_class": "InternalError",
                    "message": "internal error",
                }
            )

    try:
        heartbeat_task = asyncio.create_task(_heartbeat())
        while ws.client_state == WebSocketState.CONNECTED and not env.disconnected.is_set():
            if stream_task is not None and stream_task.done():
                await stream_task
                stream_task = None

            raw = await ws.receive_json()
            ftype = raw.get("type") if isinstance(raw, dict) else None
            if ftype != "chat":
                continue

            if stream_task is not None and not stream_task.done():
                await env.send_or_close(
                    {
                        "version": 1,
                        "type": "error",
                        "error_class": "ActiveStreamInProgress",
                        "message": "wait for the active stream to finish",
                    }
                )
                continue

            now = time.monotonic()
            cutoff = now - _TURN_WINDOW_S
            while turn_timestamps and turn_timestamps[0] < cutoff:
                turn_timestamps.popleft()
            if len(turn_timestamps) >= _TURNS_PER_MIN:
                log.info("ws_ai_chat_turn_rate_exceeded", jwt_subject=env.jwt_subject)
                await env.send_or_close(
                    {
                        "version": 1,
                        "type": "error",
                        "error_class": "TurnRateExceeded",
                        "message": f"max {_TURNS_PER_MIN} turns per minute",
                    }
                )
                continue
            turn_timestamps.append(now)

            try:
                req = CompletionRequest.model_validate(raw.get("request", {}))
            except Exception:
                await env.send_or_close(
                    {
                        "version": 1,
                        "type": "error",
                        "error_class": "InvalidRequest",
                        "message": "request payload failed validation",
                    }
                )
                continue

            stream_task = asyncio.create_task(_run_stream(req))
    except WebSocketDisconnect:
        log.info("ws_ai_chat_disconnect")
    finally:
        if stream_task is not None:
            stream_task.cancel()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        await asyncio.gather(
            *(t for t in (stream_task, heartbeat_task) if t is not None),
            return_exceptions=True,
        )
        _active_chat_connections -= 1
        await env.cleanup()
