"""WebSocket quote gateway auth and subprotocol negotiation."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from jwt.exceptions import PyJWTError
from redis.asyncio import Redis

from app.api.ws_quotes import router as ws_quotes_router
from app.core import deps
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceRouter

Message = MutableMapping[str, Any]


class FakeRedis:
    async def publish(self, _channel: str, _message: str) -> int:
        return 0


def _engine() -> QuoteEngine:
    registry = SubscriptionRegistry(
        cap_per_ws=100,
        cap_global=1000,
        sub_rate_limit_per_minute=1000,
    )
    router = SourceRouter(config={}, health=SourceHealthMap())
    return QuoteEngine(registry=registry, router=router, redis=cast(Redis, FakeRedis()))


def _app() -> FastAPI:
    app = FastAPI()
    app.state.quote_engine = _engine()
    app.include_router(ws_quotes_router)
    return app


def _valid_identity() -> Any:
    return MagicMock(email="admin@test.local", kind="cf_access_jwt")


async def _run_ws(
    *,
    headers: dict[str, str] | None = None,
    subprotocols: list[str] | None = None,
    client_host: str = "testclient",
) -> list[Message]:
    app = _app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/ws/quotes",
        "raw_path": b"/ws/quotes",
        "query_string": b"",
        "headers": _headers(headers, subprotocols),
        "client": (client_host, 50000),
        "server": ("testserver", 80),
        "subprotocols": subprotocols or [],
        "root_path": "",
    }

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(scope, receive, send), timeout=1.0)
    return messages


def _headers(
    headers: dict[str, str] | None,
    subprotocols: list[str] | None,
) -> list[tuple[bytes, bytes]]:
    raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    if subprotocols:
        raw.append((b"sec-websocket-protocol", ", ".join(subprotocols).encode()))
    return raw


@pytest.mark.asyncio
async def test_ws_upgrade_with_valid_cf_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps._verifier, "verify", MagicMock(return_value=_valid_identity()))
    messages = await _run_ws(
        headers={"Cf-Access-Jwt-Assertion": "valid"},
        subprotocols=["msgpack-v1"],
    )

    assert messages[0] == {"type": "websocket.accept", "subprotocol": "msgpack-v1", "headers": []}


@pytest.mark.asyncio
async def test_ws_upgrade_rejects_missing_jwt() -> None:
    messages = await _run_ws(subprotocols=["msgpack-v1"])

    assert messages[0]["type"] == "websocket.close"
    assert messages[0]["code"] == 1008


@pytest.mark.asyncio
async def test_ws_upgrade_rejects_invalid_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps._verifier, "verify", MagicMock(side_effect=PyJWTError("bad")))
    messages = await _run_ws(
        headers={"Cf-Access-Jwt-Assertion": "bad"},
        subprotocols=["msgpack-v1"],
    )

    assert messages[0]["type"] == "websocket.close"
    assert messages[0]["code"] == 1008


@pytest.mark.asyncio
async def test_ws_upgrade_dev_bypass_via_wg_ip() -> None:
    messages = await _run_ws(subprotocols=["msgpack-v1"], client_host="10.10.0.1")

    assert messages[0] == {"type": "websocket.accept", "subprotocol": "msgpack-v1", "headers": []}


@pytest.mark.asyncio
async def test_ws_subprotocol_msgpack_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps._verifier, "verify", MagicMock(return_value=_valid_identity()))
    accepted = await _run_ws(
        headers={"Cf-Access-Jwt-Assertion": "valid"},
        subprotocols=["msgpack-v1"],
    )
    rejected = await _run_ws(headers={"Cf-Access-Jwt-Assertion": "valid"})

    assert accepted[0] == {"type": "websocket.accept", "subprotocol": "msgpack-v1", "headers": []}
    assert rejected[0]["type"] == "websocket.close"
    assert rejected[0]["code"] == 1002
