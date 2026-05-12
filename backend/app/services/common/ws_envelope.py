from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketException, status
from starlette.websockets import WebSocketDisconnect


@dataclass(frozen=True)
class WSEnvelopeConfig:
    allowed_origins: frozenset[str]
    max_connections: int
    active_counter: Callable[[], int]
    send_timeout_s: float
    heartbeat_s: float


class WSEnvelope:
    jwt_subject: str | None
    disconnected: asyncio.Event

    def __init__(self, ws: WebSocket, cfg: WSEnvelopeConfig) -> None:
        self._ws = ws
        self._cfg = cfg
        self.jwt_subject = None
        self.disconnected = asyncio.Event()
        self._recv_task: asyncio.Task[None] | None = None

    async def handshake(self, *, auth: Callable[[WebSocket], Awaitable[str | None]]) -> bool:
        if not self._allowed_origin():
            await self._ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin")
            return False

        if self._cfg.active_counter() >= self._cfg.max_connections:
            await self._ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="capacity")
            return False

        try:
            jwt_subject = await auth(self._ws)
        except WebSocketException:
            return False

        if jwt_subject is not None:
            self.jwt_subject = jwt_subject
        await self._ws.accept()
        return True

    def _allowed_origin(self) -> bool:
        origin = self._ws.headers.get("origin", "")
        if not origin:
            client_host = self._ws.client.host if self._ws.client else ""
            return client_host == "10.10.0.1"
        return origin in self._cfg.allowed_origins

    def start_recv_drain(self) -> None:
        async def _recv_drain() -> None:
            try:
                while True:
                    await self._ws.receive_text()
            except WebSocketDisconnect:
                self.disconnected.set()
            except ConnectionResetError:
                self.disconnected.set()
            except RuntimeError:
                self.disconnected.set()

        self._recv_task = asyncio.create_task(_recv_drain())

    async def send_or_close(self, payload: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(
                self._ws.send_json(payload),
                timeout=self._cfg.send_timeout_s,
            )
        except TimeoutError:
            await self._ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="send-timeout")
            return False
        except WebSocketDisconnect:
            self.disconnected.set()
            return False
        return True

    async def cleanup(self) -> None:
        if self._recv_task is None:
            return
        self._recv_task.cancel()
        try:
            await self._recv_task
        except asyncio.CancelledError:
            pass


def make_ws_endpoint(ws: WebSocket, cfg: WSEnvelopeConfig) -> WSEnvelope:
    return WSEnvelope(ws, cfg)
