"""Alpaca streaming event sources.

Phase 8c chunk-C scope: helpers for a future crypto-specific order event stream.
Currently unused in the production handler — `TradingStream.subscribe_trade_updates`
in handlers.py covers BOTH equity AND crypto order updates (alpaca-py does not
expose a separate CryptoTradeStream API). When/if alpaca-py adds a dedicated
crypto trade stream, wire `crypto_order_event_source` into
`AlpacaServicer._ensure_order_event_subscription` as a second feed merging into
the existing per-account queue. The `_map_crypto_trade_update` symbol
canonicalization is the load-bearing piece kept here for the future wiring.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from typing import Any

import structlog
from alpaca.data.live.crypto import CryptoDataStream

from sidecar_alpaca import config
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


async def crypto_order_event_source(
    stream_factory: Callable[[], Any],
) -> AsyncGenerator[dict[str, str]]:
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
    api_key: str, api_secret: str, crypto_feed: str = config.CRYPTO_LOCATION
) -> CryptoDataStream:
    return CryptoDataStream(api_key, api_secret, feed=crypto_feed)
