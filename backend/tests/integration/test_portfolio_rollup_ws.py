"""Phase 10b.2 §6 — /ws/portfolio/rollup integration tests.

Mirrors test_ws_bars.py raw-ASGI pattern. WG-dev bypass (client_host=10.10.0.1)
in app.api.ws_auth.require_admin_jwt_ws lets these tests skip JWT mocking.
PortfolioRollupService is monkey-patched to a stub so we don't need a real DB.
"""

from __future__ import annotations

import asyncio
import json
import typing
from collections.abc import MutableMapping
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI

from app.api.ws_portfolio import _MAX_WS_CONNECTIONS
from app.api.ws_portfolio import router as ws_portfolio_router

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

Message = MutableMapping[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(fake_redis: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_portfolio_router)
    app.state.redis = fake_redis
    app.state.cors_origins = ["http://localhost:5173"]
    return app


def _make_scope(
    path: str = "/ws/portfolio/rollup",
    *,
    client_host: str = "10.10.0.1",
    origin: str | None = None,
) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"base=GBP",
        "headers": headers,
        "client": (client_host, 50001),
        "server": ("testserver", 80),
        "subprotocols": [],
        "root_path": "",
    }


class _FakeRollup:
    """Stub Pydantic-like RollupLive. The gateway calls .model_dump(mode='json')
    and accesses .stale_accounts.
    """

    stale_accounts: typing.ClassVar[list[str]] = []

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "base_currency": "GBP",
            "total_nlv_base": "1000.00",
            "total_realized_today_base": "0",
            "total_unrealized_base": "0",
            "history_since": "2026-05-12T00:00:00+00:00",
            "accounts": [],
            "exposure_by_asset_class": [],
            "fx_rates": {},
            "stale_accounts": [],
            "fx_stale_accounts": [],
            "partial": False,
        }


class _FakeService:
    """Stub PortfolioRollupService — same constructor signature, async compute_live."""

    def __init__(self, session: Any, redis: Any) -> None:
        self._session = session
        self._redis = redis

    async def compute_live(self, base: str) -> _FakeRollup:
        return _FakeRollup()


def _patch_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.ws_portfolio.PortfolioRollupService", _FakeService)


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


# ---------------------------------------------------------------------------
# Test 1 — connect + initial snapshot frame
# ---------------------------------------------------------------------------


async def test_connects_and_emits_initial_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _patch_service(monkeypatch)
    app = _make_app(fake_redis)
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(origin="http://localhost:5173")

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.send":
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    accept = next((m for m in messages if m["type"] == "websocket.accept"), None)
    assert accept is not None, f"No accept; got: {[m['type'] for m in messages]}"

    snapshot_frame = next(
        (json.loads(m["text"]) for m in messages if m["type"] == "websocket.send" and "text" in m),
        None,
    )
    assert snapshot_frame is not None
    assert snapshot_frame["version"] == 1
    assert snapshot_frame["type"] == "snapshot"
    assert "payload" in snapshot_frame
    assert snapshot_frame["payload"]["base_currency"] == "GBP"


# ---------------------------------------------------------------------------
# Test 2 — CSWSH origin rejection (1008 origin)
# ---------------------------------------------------------------------------


async def test_cswsh_rejects_cross_origin(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Browser on attacker.com cannot upgrade — origin check fires pre-accept."""
    app = _make_app(fake_redis)
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(origin="https://attacker.com")

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    accept = next((m for m in messages if m["type"] == "websocket.accept"), None)
    assert accept is None, "Should not accept cross-origin upgrade"
    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None and close["code"] == 1008
    assert close.get("reason") == "origin"


# ---------------------------------------------------------------------------
# Test 3 — connection cap rejects with 1008 capacity (BEFORE origin check)
# ---------------------------------------------------------------------------


async def test_capacity_cap_rejects_with_1008(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    import app.api.ws_portfolio as ws_portfolio_module

    monkeypatch.setattr(ws_portfolio_module, "_active_connections", _MAX_WS_CONNECTIONS)

    app = _make_app(fake_redis)
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(origin="http://localhost:5173")

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    accept = next((m for m in messages if m["type"] == "websocket.accept"), None)
    assert accept is None, "Should not accept when at capacity"
    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None and close["code"] == 1008
    assert close.get("reason") == "capacity"


# ---------------------------------------------------------------------------
# Test 4 — disconnect cleans up _active_connections counter
# ---------------------------------------------------------------------------


async def test_disconnect_cleans_up_active_connections(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    import app.api.ws_portfolio as ws_portfolio_module

    _patch_service(monkeypatch)
    baseline = ws_portfolio_module._active_connections

    app = _make_app(fake_redis)
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(origin="http://localhost:5173")

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.send":
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    assert ws_portfolio_module._active_connections == baseline, (
        f"Counter leaked: baseline={baseline} after={ws_portfolio_module._active_connections}"
    )
