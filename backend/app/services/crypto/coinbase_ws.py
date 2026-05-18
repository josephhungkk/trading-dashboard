from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from app.core import metrics
from app.services.crypto.book_manager import OrderBook

log = structlog.get_logger(__name__)

_RECONNECT_DELAYS = [1, 2, 5, 15, 30]


def canonical_id_for(product_id: str) -> str:
    return product_id.replace("-", ".")


class CoinbaseWsAdapter:
    def __init__(
        self,
        redis: Any,
        config_getter: Callable[[], Awaitable[list[str]]],
    ) -> None:
        self._redis = redis
        self._config_getter = config_getter
        self._books: dict[str, OrderBook] = {}
        self._ws: Any = None
        self._snapshot_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        delay_idx = 0
        try:
            while True:
                try:
                    product_ids = await self._config_getter()
                    for pid in product_ids:
                        cid = canonical_id_for(pid)
                        if cid not in self._books:
                            self._books[cid] = OrderBook()

                    uri = "wss://advanced-trade-ws.coinbase.com/"
                    async with websockets.connect(uri) as ws:  # type: ignore[attr-defined]
                        self._ws = ws
                        delay_idx = 0
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "channel": "ticker",
                                    "product_ids": product_ids,
                                }
                            )
                        )
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "channel": "level2",
                                    "product_ids": product_ids,
                                }
                            )
                        )

                        async for raw_msg in ws:
                            await self._dispatch(raw_msg)

                except asyncio.CancelledError:
                    raise
                except (ConnectionClosed, websockets.exceptions.WebSocketException) as exc:  # type: ignore[attr-defined]
                    metrics.coinbase_ws_reconnects_total.inc()
                    log.warning("ws_connection_closed", error=str(exc))
                    await self._close_ws()
                    await asyncio.sleep(
                        _RECONNECT_DELAYS[min(delay_idx, len(_RECONNECT_DELAYS) - 1)]
                    )
                    delay_idx = min(delay_idx + 1, len(_RECONNECT_DELAYS) - 1)
                except Exception as exc:
                    metrics.coinbase_ws_reconnects_total.inc()
                    log.error("ws_unexpected_error", error=str(exc))
                    await self._close_ws()
                    await asyncio.sleep(
                        _RECONNECT_DELAYS[min(delay_idx, len(_RECONNECT_DELAYS) - 1)]
                    )
                    delay_idx = min(delay_idx + 1, len(_RECONNECT_DELAYS) - 1)
        finally:
            await self._close_ws()
            if self._snapshot_task and not self._snapshot_task.done():
                self._snapshot_task.cancel()
                try:
                    await self._snapshot_task
                except asyncio.CancelledError:
                    pass

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _dispatch(self, raw_msg: str | bytes) -> None:
        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            metrics.coinbase_ws_messages_total.labels(
                channel="unknown", outcome="parse_error"
            ).inc()
            log.warning("ws_json_parse_error", payload=str(raw_msg)[:200])
            return

        channel = msg.get("channel")
        if channel == "ticker":
            await self._handle_ticker(msg)
        elif channel == "l2_data":
            await self._handle_l2(msg)
        elif channel == "subscriptions":
            log.info("ws_subscription_confirmed", msg=msg)
        else:
            metrics.coinbase_ws_messages_total.labels(channel="unknown", outcome="ignored").inc()
            log.warning("ws_unknown_channel", channel=channel)

    async def _handle_ticker(self, msg: dict[str, Any]) -> None:
        events = msg.get("events", [])
        if not events:
            return
        event = events[0]
        product_id = event.get("product_id", "")
        canonical_id = canonical_id_for(product_id)
        payload = {
            "bid": event.get("best_bid"),
            "ask": event.get("best_ask"),
            "price": event.get("price"),
            "canonical_id": canonical_id,
        }
        try:
            await self._redis.publish(f"quote.coinbase.{canonical_id}", json.dumps(payload))
        except Exception as exc:
            log.error("ticker_publish_failed", canonical_id=canonical_id, error=str(exc))
        metrics.coinbase_ws_messages_total.labels(channel="ticker", outcome="ok").inc()

    async def _handle_l2(self, msg: dict[str, Any]) -> None:
        product_id = msg.get("product_id", "")
        canonical_id = canonical_id_for(product_id)
        book = self._books.get(canonical_id)
        if book is None:
            log.warning("l2_no_book", canonical_id=canonical_id)
            return

        for event in msg.get("events", []):
            event_type = event.get("type")
            updates = event.get("updates", [])

            if event_type == "snapshot":
                book = OrderBook()
                self._books[canonical_id] = book
                for upd in updates:
                    side = "ask" if upd.get("side") == "offer" else upd.get("side", "bid")
                    price = Decimal(upd.get("price_level", "0"))
                    qty = Decimal(upd.get("new_quantity", "0"))
                    seq = int(upd.get("event_sequence_num") or 0)
                    book.apply_delta(side, price, qty, seq)

            elif event_type == "update":
                gap_detected = False
                for upd in updates:
                    raw_seq = upd.get("event_sequence_num")
                    received_seq = int(raw_seq) if raw_seq is not None else None

                    if (
                        received_seq is not None
                        and book.last_seq != 0
                        and received_seq != book.last_seq + 1
                    ):
                        metrics.coinbase_book_sequence_gap_total.labels(
                            canonical_id=canonical_id
                        ).inc()
                        log.warning(
                            "l2_sequence_gap",
                            canonical_id=canonical_id,
                            last=book.last_seq,
                            received=received_seq,
                        )
                        self._books[canonical_id] = OrderBook()
                        book = self._books[canonical_id]
                        await self._resubscribe_l2(product_id)
                        gap_detected = True
                        break

                    side = "ask" if upd.get("side") == "offer" else upd.get("side", "bid")
                    price = Decimal(upd.get("price_level", "0"))
                    qty = Decimal(upd.get("new_quantity", "0"))
                    seq = int(raw_seq) if raw_seq is not None else book.last_seq
                    book.apply_delta(side, price, qty, seq)

                    t0 = time.monotonic()
                    try:
                        await self._redis.xadd(
                            f"crypto:book:{canonical_id}",
                            {
                                b"side": side,
                                b"price": str(price),
                                b"qty": str(qty),
                                b"seq": str(seq),
                            },
                            maxlen=1000,
                        )
                        metrics.coinbase_book_publish_total.labels(canonical_id=canonical_id).inc()
                    except Exception as exc:
                        log.error("l2_xadd_failed", canonical_id=canonical_id, error=str(exc))
                    metrics.coinbase_book_lag_seconds.observe(time.monotonic() - t0)

                if gap_detected:
                    continue
            else:
                log.warning("l2_unknown_event_type", event_type=event_type)

        metrics.coinbase_ws_messages_total.labels(channel="l2_data", outcome="ok").inc()

    async def _resubscribe_l2(self, product_id: str) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {"type": "unsubscribe", "channel": "level2", "product_ids": [product_id]}
                )
            )
            await self._ws.send(
                json.dumps({"type": "subscribe", "channel": "level2", "product_ids": [product_id]})
            )
        except Exception as exc:
            log.error("l2_resubscribe_failed", product_id=product_id, error=str(exc))

    async def _snapshot_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            for canonical_id, book in list(self._books.items()):
                try:
                    snap = book.snapshot(depth=100)
                    await self._redis.hset(
                        f"crypto:book:snap:{canonical_id}",
                        mapping={
                            "bids": json.dumps([[str(p), str(q)] for p, q in snap["bids"]]),
                            "asks": json.dumps([[str(p), str(q)] for p, q in snap["asks"]]),
                        },
                    )
                except Exception as exc:
                    log.error("snapshot_publish_failed", canonical_id=canonical_id, error=str(exc))
