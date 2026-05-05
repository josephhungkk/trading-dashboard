"""Alpaca StreamQuotes subscribe vs resync reconnect contract tests."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.metrics import ALPACA_WS_RECONNECT_TOTAL
from sidecar_alpaca.streamer import AlpacaStreamer


async def _streamer() -> AlpacaStreamer:
    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return AlpacaStreamer(auth)


def _reconnect_value(reason: str) -> float:
    return ALPACA_WS_RECONNECT_TOTAL.labels(
        endpoint="iex", reason=reason
    )._value.get()


@pytest.mark.asyncio
async def test_resync_diff_only(monkeypatch) -> None:
    streamer = await _streamer()
    streamer._iex_active = {"A", "B"}
    streamer._iex_ws = AsyncMock()
    subscribe = AsyncMock()
    unsubscribe = AsyncMock()
    restart = AsyncMock()
    monkeypatch.setattr(streamer, "_send_ws_subscribe", subscribe)
    monkeypatch.setattr(streamer, "_send_ws_unsubscribe", unsubscribe)
    monkeypatch.setattr(streamer, "_restart_iex_loop", restart)

    await streamer.on_resync(["B", "C"])

    subscribe.assert_awaited_once_with(["C"])
    unsubscribe.assert_awaited_once_with(["A"])
    restart.assert_not_awaited()
    assert streamer._iex_active == {"B", "C"}


@pytest.mark.asyncio
async def test_subscribe_triggers_reconnect(monkeypatch) -> None:
    streamer = await _streamer()
    monkeypatch.setattr(streamer, "_send_ws_subscribe", AsyncMock())
    before = _reconnect_value("subscribe_replay")

    await streamer.on_subscribe(["X"])

    after = _reconnect_value("subscribe_replay")
    assert after == before + 1
