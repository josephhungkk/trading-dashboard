"""Alpaca IEX streamer tests."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.metrics import ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL
from sidecar_alpaca.streamer import AlpacaStreamer


class FakeIexWebSocket:
    def __init__(self, quote_ready: asyncio.Event, auth_sent: asyncio.Event) -> None:
        self.sent: list[dict[str, Any]] = []
        self.send = AsyncMock(side_effect=self._send)
        self.closed = False
        self._quote_ready = quote_ready
        self._auth_sent = auth_sent
        self._recv_count = 0

    async def __aenter__(self) -> FakeIexWebSocket:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def _send(self, message: str) -> None:
        frame = json.loads(message)
        self.sent.append(frame)
        if frame.get("action") == "auth":
            self._auth_sent.set()

    async def recv(self) -> str:
        self._recv_count += 1
        if self._recv_count == 1:
            return json.dumps([{"T": "success", "msg": "authenticated"}])
        await self._quote_ready.wait()
        if self._recv_count == 2:
            return json.dumps([{"T": "q", "S": "AAPL", "bp": 150.5, "ap": 150.55}])
        await asyncio.sleep(60)
        return "[]"

    async def close(self) -> None:
        self.closed = True


def _counter_value(reason: str) -> float:
    return ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
        endpoint="iex", reason=reason
    )._value.get()


async def _auth_cache() -> AuthCache:
    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return auth


@pytest.mark.asyncio
async def test_streamer_iex_subscribes_and_dispatches_tick(monkeypatch) -> None:
    quote_ready = asyncio.Event()
    auth_sent = asyncio.Event()
    tick_received = asyncio.Event()
    websockets: list[FakeIexWebSocket] = []

    def connect(*_args: object, **_kwargs: object) -> FakeIexWebSocket:
        ws = FakeIexWebSocket(quote_ready, auth_sent)
        websockets.append(ws)
        return ws

    received: list[Any] = []
    streamer = AlpacaStreamer(await _auth_cache())

    async def tick_callback(quote: Any) -> None:
        received.append(quote)
        tick_received.set()

    streamer.tick_callback = tick_callback
    monkeypatch.setattr("sidecar_alpaca.streamer.websockets.connect", connect)

    await streamer.start()
    try:
        await asyncio.wait_for(auth_sent.wait(), timeout=1)

        await streamer.on_subscribe(["AAPL"])
        quote_ready.set()
        await asyncio.wait_for(tick_received.wait(), timeout=1)

        sent = [frame for ws in websockets for frame in ws.sent]
        subscribe_frames = [
            frame for frame in sent if frame.get("action") == "subscribe"
        ]
        assert subscribe_frames
        assert subscribe_frames[-1]["quotes"] == ["AAPL"]
        assert received[0].canonical_id == "AAPL"
        assert received[0].source == "alpaca"
        assert received[0].bid == "150.5"
        assert received[0].ask == "150.55"
        assert received[0].received_at.seconds > 0
    finally:
        await streamer.stop()


@pytest.mark.asyncio
async def test_streamer_iex_cap_at_30() -> None:
    streamer = AlpacaStreamer(await _auth_cache())
    streamer._iex_active = {f"SYM{i}" for i in range(30)}
    before = _counter_value("cap_exceeded")

    await streamer.on_subscribe(["NEW"])

    after = _counter_value("cap_exceeded")
    assert after == before + 1
    assert "NEW" not in streamer._iex_active
