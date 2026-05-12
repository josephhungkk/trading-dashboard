from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketException, status
from starlette.websockets import WebSocketDisconnect

from app.services.common.ws_envelope import WSEnvelopeConfig, make_ws_endpoint

pytestmark = pytest.mark.no_db


def _make_ws(*, origin: str = "http://localhost:5173", client_host: str = "127.0.0.1") -> MagicMock:
    ws = MagicMock()
    ws.headers = {"origin": origin} if origin else {}
    ws.client = MagicMock()
    ws.client.host = client_host
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


def _make_cfg(*, active: int = 0) -> WSEnvelopeConfig:
    return WSEnvelopeConfig(
        allowed_origins=frozenset({"http://localhost:5173"}),
        max_connections=2,
        active_counter=lambda: active,
        send_timeout_s=0.01,
        heartbeat_s=30.0,
    )


@pytest.mark.asyncio
async def test_origin_rejected() -> None:
    ws = _make_ws(origin="https://attacker.example")
    env = make_ws_endpoint(ws, _make_cfg())

    accepted = await env.handshake(auth=AsyncMock(return_value="admin@example.com"))

    assert accepted is False
    ws.close.assert_awaited_once_with(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="origin",
    )
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_cap() -> None:
    ws = _make_ws()
    env = make_ws_endpoint(ws, _make_cfg(active=2))

    accepted = await env.handshake(auth=AsyncMock(return_value="admin@example.com"))

    assert accepted is False
    ws.close.assert_awaited_once_with(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="capacity",
    )
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_fail() -> None:
    ws = _make_ws()
    auth = AsyncMock(side_effect=WebSocketException(code=status.WS_1008_POLICY_VIOLATION))
    env = make_ws_endpoint(ws, _make_cfg())

    accepted = await env.handshake(auth=auth)

    assert accepted is False
    ws.close.assert_not_awaited()
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_timeout() -> None:
    ws = _make_ws()
    ws.send_json = AsyncMock(side_effect=asyncio.TimeoutError)
    env = make_ws_endpoint(ws, _make_cfg())

    sent = await env.send_or_close({"version": 1, "type": "snapshot"})

    assert sent is False
    ws.close.assert_awaited_once_with(
        code=status.WS_1011_INTERNAL_ERROR,
        reason="send-timeout",
    )


@pytest.mark.asyncio
async def test_recv_drain_disconnect() -> None:
    ws = _make_ws()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect)
    env = make_ws_endpoint(ws, _make_cfg())

    env.start_recv_drain()
    await asyncio.wait_for(env.disconnected.wait(), timeout=0.5)
    await env.cleanup()

    assert env.disconnected.is_set()


@pytest.mark.asyncio
async def test_wg_bypass_empty_origin() -> None:
    ws = _make_ws(origin="", client_host="10.10.0.1")
    env = make_ws_endpoint(ws, _make_cfg())

    accepted = await env.handshake(auth=AsyncMock(return_value="dev-bypass"))

    assert accepted is True
    assert env.jwt_subject == "dev-bypass"
    ws.accept.assert_awaited_once_with()
