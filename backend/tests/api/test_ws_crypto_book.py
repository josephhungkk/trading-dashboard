"""Tests for WS /ws/crypto/book/{canonical_id}."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, MutableMapping
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI

import app.api.ws_crypto as ws_crypto
from app.api.ws_crypto import router

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]

Message = MutableMapping[str, Any]


@pytest.fixture(autouse=True)
def _reset_ws_counter() -> Iterator[None]:
    ws_crypto._active_connections = 0
    yield
    ws_crypto._active_connections = 0


def _make_app(redis: fakeredis.aioredis.FakeRedis) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.redis = redis
    app.state.cors_origins = frozenset({"http://testserver"})
    return app


def _make_scope(canonical_id: str) -> dict[str, Any]:
    path = f"/ws/crypto/book/{canonical_id}"
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"origin", b"http://testserver")],
        "client": ("10.10.0.1", 50001),
        "server": ("testserver", 80),
        "subprotocols": [],
        "root_path": "",
    }


async def _receive_first_json(app: FastAPI, canonical_id: str) -> dict[str, Any]:
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    sent_disconnect = False
    first_payload: dict[str, Any] | None = None

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        nonlocal first_payload, sent_disconnect
        messages.append(message)
        if message.get("type") != "websocket.send" or "text" not in message:
            return
        if first_payload is None:
            first_payload = json.loads(str(message["text"]))
        if not sent_disconnect:
            sent_disconnect = True
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(_make_scope(canonical_id), receive, send), timeout=3.0)
    assert first_payload is not None, f"no JSON frame received; messages={messages}"
    return first_payload


async def test_crypto_book_ws_sends_initial_snapshot_from_redis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        app = _make_app(redis)
        await redis.hset(
            "crypto:book:snap:BTC.USD",
            mapping={
                "bids": json.dumps([["50000", "1.2"]]),
                "asks": json.dumps([["50100", "0.8"]]),
            },
        )

        msg = await _receive_first_json(app, "BTC.USD")
    finally:
        await redis.aclose()

    assert msg["type"] == "book_snapshot"
    assert len(msg["bids"]) > 0


async def test_crypto_book_ws_sends_empty_snapshot_on_miss() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        app = _make_app(redis)
        msg = await _receive_first_json(app, "ETH.USD")
    finally:
        await redis.aclose()

    assert msg["type"] == "book_snapshot"
    assert msg["bids"] == []
    assert msg["asks"] == []
