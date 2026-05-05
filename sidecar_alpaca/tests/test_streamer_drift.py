"""Alpaca subscribe-rejection drift detection tests."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.metrics import ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL
from sidecar_alpaca.streamer import AlpacaStreamer


def _counter_value(endpoint: str, reason: str) -> float:
    return ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
        endpoint=endpoint, reason=reason
    )._value.get()


def test_iex_error_frame_removes_symbol_from_active() -> None:
    streamer = AlpacaStreamer.for_tests()
    streamer._iex_active = {"AAPL", "TSLA"}
    streamer._iex_symbol_map = {"AAPL": "stock:AAPL:US", "TSLA": "stock:TSLA:US"}
    before = _counter_value("iex", "cap_exceeded")

    streamer._dispatch_frame(
        json.dumps([{"T": "error", "code": 410, "msg": "AAPL rate-limited"}])
    )

    after = _counter_value("iex", "cap_exceeded")
    assert "AAPL" not in streamer._iex_active
    assert "TSLA" in streamer._iex_active
    assert after == before + 1


def test_crypto_error_frame_removes_symbol_from_active() -> None:
    streamer = AlpacaStreamer.for_tests()
    streamer._crypto_active = {"BTC/USD", "ETH/USD"}
    before = _counter_value("crypto", "cap_exceeded")

    streamer._dispatch_frame(
        json.dumps([{"T": "error", "code": 410, "msg": "BTC/USD rate-limited"}]),
        endpoint="crypto",
    )

    after = _counter_value("crypto", "cap_exceeded")
    assert "BTC/USD" not in streamer._crypto_active
    assert "ETH/USD" in streamer._crypto_active
    assert after == before + 1


@pytest.mark.asyncio
async def test_drift_emits_sentinel_quote_message() -> None:
    streamer = AlpacaStreamer.for_tests()
    streamer._iex_active = {"AAPL"}
    streamer._iex_symbol_map = {"AAPL": "stock:AAPL:US"}
    received: list[Any] = []
    tick_received = asyncio.Event()

    async def tick_callback(quote: Any) -> None:
        received.append(quote)
        tick_received.set()

    streamer.tick_callback = tick_callback

    streamer._dispatch_frame(
        json.dumps([{"T": "error", "code": 410, "msg": "AAPL rate-limited"}])
    )

    await asyncio.wait_for(tick_received.wait(), timeout=1)
    assert received[0].canonical_id == "stock:AAPL:US"
    assert received[0].source == "alpaca"
    assert received[0].raw_payload == b'{"drift":"cap_exceeded"}'
