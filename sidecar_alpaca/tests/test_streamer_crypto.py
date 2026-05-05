"""Alpaca crypto streamer tests."""

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
from sidecar_alpaca.normalize import (
    alpaca_crypto_to_canonical,
    canonical_to_alpaca_crypto,
)
from sidecar_alpaca.streamer import AlpacaStreamer


async def _auth_cache() -> AuthCache:
    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return auth


def _counter_value(reason: str) -> float:
    return ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
        endpoint="crypto", reason=reason
    )._value.get()


def test_canonical_to_alpaca_crypto() -> None:
    assert canonical_to_alpaca_crypto("crypto:BTC:US") == "BTC/USD"
    with pytest.raises(ValueError, match="not a crypto canonical_id"):
        canonical_to_alpaca_crypto("stock:AAPL:US")


def test_alpaca_crypto_to_canonical() -> None:
    assert alpaca_crypto_to_canonical("BTC/USD") == "crypto:BTC:US"


@pytest.mark.asyncio
async def test_crypto_subscribe_sends_alpaca_pair(monkeypatch) -> None:
    streamer = AlpacaStreamer(await _auth_cache())
    ws = AsyncMock()
    streamer._crypto_ws = ws
    monkeypatch.setattr(streamer, "_restart_crypto_loop", AsyncMock())

    await streamer.on_subscribe_crypto(["crypto:BTC:US", "crypto:ETH:US"])

    ws.send.assert_awaited_once()
    payload = json.loads(ws.send.await_args.args[0])
    assert payload == {
        "action": "subscribe",
        "trades": [],
        "quotes": ["BTC/USD", "ETH/USD"],
        "bars": [],
    }


@pytest.mark.asyncio
async def test_crypto_cap_at_30_for_endpoint() -> None:
    streamer = AlpacaStreamer(await _auth_cache())
    streamer._crypto_active = {f"SYM{i}/USD" for i in range(30)}
    before = _counter_value("cap_exceeded")

    await streamer.on_subscribe_crypto(["crypto:NEW:US"])

    after = _counter_value("cap_exceeded")
    assert after == before + 1
    assert "NEW/USD" not in streamer._crypto_active


@pytest.mark.asyncio
async def test_crypto_tick_routed_with_canonical_id() -> None:
    streamer = AlpacaStreamer(await _auth_cache())
    tick_received = asyncio.Event()
    received: list[Any] = []

    async def tick_callback(quote: Any) -> None:
        received.append(quote)
        tick_received.set()

    streamer.tick_callback = tick_callback
    streamer._dispatch_frame(
        json.dumps(
            [
                {
                    "T": "q",
                    "S": "BTC/USD",
                    "bp": 50000,
                    "ap": 50001,
                    "t": "2026-05-05T12:00:00Z",
                }
            ]
        ),
        endpoint="crypto",
    )

    await asyncio.wait_for(tick_received.wait(), timeout=1)
    assert received[0].canonical_id == "crypto:BTC:US"
    assert received[0].source == "alpaca"
    assert received[0].bid == "50000"
    assert received[0].ask == "50001"
