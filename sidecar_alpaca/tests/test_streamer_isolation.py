"""Endpoint task isolation tests for Alpaca streamer."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.metrics import ALPACA_WS_RECONNECT_TOTAL
from sidecar_alpaca.streamer import AlpacaStreamer


def _reconnect_value(reason: str) -> float:
    return ALPACA_WS_RECONNECT_TOTAL.labels(
        endpoint="iex", reason=reason
    )._value.get()


@pytest.mark.asyncio
async def test_iex_loop_crash_does_not_cancel_crypto(monkeypatch) -> None:
    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    streamer = AlpacaStreamer(auth)
    before = _reconnect_value("loop_crash")

    async def fake_crypto_loop() -> None:
        await asyncio.sleep(60)

    def connect(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("iex boom")

    monkeypatch.setattr(streamer, "_crypto_loop", fake_crypto_loop)
    monkeypatch.setattr("sidecar_alpaca.streamer.websockets.connect", connect)

    task = asyncio.create_task(streamer._supervisor_loop())
    try:
        for _ in range(100):
            if _reconnect_value("loop_crash") > before:
                break
            await asyncio.sleep(0.01)

        assert _reconnect_value("loop_crash") >= before + 1
        assert streamer._crypto_task is not None
        assert not streamer._crypto_task.done()
    finally:
        streamer._shutting_down = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if streamer._crypto_task is not None:
            streamer._crypto_task.cancel()
            await asyncio.gather(streamer._crypto_task, return_exceptions=True)
