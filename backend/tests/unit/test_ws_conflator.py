"""WSConflator rate and slow-client behavior."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast

import msgpack  # type: ignore[import-untyped]
import pytest
from fastapi import WebSocket

from app._generated.broker.v1 import broker_pb2 as pb
from app.api.ws_quotes import WSConflator


class FakeWebSocket:
    def __init__(self, *, send_delay: float = 0.0) -> None:
        self.send_delay = send_delay
        self.sent: list[bytes] = []
        self.closed: list[tuple[int, str | None]] = []

    async def send_bytes(self, data: bytes) -> None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append((code, reason))


def _q(canonical_id: str, last: str = "100") -> pb.QuoteMessage:
    return pb.QuoteMessage(canonical_id=canonical_id, source="schwab", last=last)


async def _run_for(conflator: WSConflator, seconds: float) -> None:
    task = asyncio.create_task(conflator.run())
    try:
        await asyncio.sleep(seconds)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_focused_drains_at_10hz() -> None:
    ws = FakeWebSocket()
    conflator = WSConflator(cast(WebSocket, ws), focused_default="stock:AAPL:US")
    for i in range(20):
        conflator.on_quote(_q("stock:AAPL:US", str(i)))

    await _run_for(conflator, 0.5)

    assert len(ws.sent) <= 5


@pytest.mark.asyncio
async def test_background_drains_at_4hz() -> None:
    ws = FakeWebSocket()
    conflator = WSConflator(cast(WebSocket, ws))
    for i in range(20):
        conflator.on_quote(_q("stock:AAPL:US", str(i)))

    await _run_for(conflator, 0.5)

    assert len(ws.sent) <= 2


@pytest.mark.asyncio
async def test_slow_client_send_timeout_closes_ws() -> None:
    ws = FakeWebSocket(send_delay=2.1)
    conflator = WSConflator(cast(WebSocket, ws), focused_default="stock:AAPL:US")
    conflator.on_quote(_q("stock:AAPL:US"))

    await _run_for(conflator, 2.4)

    assert ws.closed


@pytest.mark.asyncio
async def test_focus_change_promotes_symbol() -> None:
    ws = FakeWebSocket()
    conflator = WSConflator(cast(WebSocket, ws))
    conflator.set_focus("stock:AAPL:US")
    task = asyncio.create_task(conflator.run())
    try:
        for i in range(10):
            conflator.on_quote(_q("stock:AAPL:US", str(i)))
            await asyncio.sleep(0.03)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(ws.sent) >= 2


@pytest.mark.asyncio
async def test_latest_only_conflation() -> None:
    ws = FakeWebSocket()
    conflator = WSConflator(cast(WebSocket, ws))
    for i in range(5):
        conflator.on_quote(_q("stock:AAPL:US", str(i)))

    await _run_for(conflator, 0.3)

    assert len(ws.sent) <= 2
    frames: list[dict[str, Any]] = [msgpack.unpackb(item, raw=False) for item in ws.sent]
    assert frames[-1]["q"]["last"] == "4"
