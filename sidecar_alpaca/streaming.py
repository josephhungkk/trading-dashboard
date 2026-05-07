"""Alpaca streaming event sources."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog
from alpaca.data.live.crypto import CryptoDataStream

from sidecar_alpaca.symbol_util import canonical_crypto_symbol

log = structlog.get_logger(module="sidecar_alpaca.streaming")

_CRYPTO_ORDER_EVENT_QUEUE_MAXSIZE = 2048


def _map_crypto_trade_update(payload: dict[str, Any]) -> dict[str, str]:
    order = payload.get("order", {})
    return {
        "asset_class": "CRYPTO",
        "external_order_id": str(order.get("id", "")),
        "symbol": canonical_crypto_symbol(str(order.get("symbol", ""))),
        "status": str(payload.get("event", "")).upper(),
    }


async def crypto_order_event_source(stream_factory):
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(
        maxsize=_CRYPTO_ORDER_EVENT_QUEUE_MAXSIZE
    )

    async def on_trade_update(payload: dict[str, Any]) -> None:
        try:
            event = _map_crypto_trade_update(payload)
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(event)
        except Exception as exc:
            log.warning("crypto_trade_update_callback_failed", exc_info=exc)

    stream = stream_factory()
    stream.subscribe_trade_updates(on_trade_update)
    task = asyncio.create_task(stream.run())
    try:
        while True:
            yield await queue.get()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_crypto_stream(
    api_key: str, api_secret: str, crypto_feed: str = "us"
) -> CryptoDataStream:
    return CryptoDataStream(api_key, api_secret, feed=crypto_feed)
