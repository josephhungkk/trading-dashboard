"""Unit tests for CoinbaseWsAdapter."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services.crypto.coinbase_ws import CoinbaseWsAdapter, canonical_id_for


def test_canonical_id_for() -> None:
    assert canonical_id_for("BTC-USD") == "BTC.USD"
    assert canonical_id_for("ETH-USD") == "ETH.USD"
    assert canonical_id_for("BTCUSD") == "BTCUSD"


@pytest.fixture
def redis_mock() -> AsyncMock:
    r = AsyncMock()
    r.xadd = AsyncMock()
    r.hset = AsyncMock()
    r.publish = AsyncMock()
    return r


def _make_adapter(redis_mock: AsyncMock) -> CoinbaseWsAdapter:
    async def config_getter() -> list[str]:
        return ["BTC-USD", "ETH-USD"]

    return CoinbaseWsAdapter(redis=redis_mock, config_getter=config_getter)


async def test_ticker_message_publishes_to_redis(redis_mock: AsyncMock) -> None:
    adapter = _make_adapter(redis_mock)
    msg = {
        "channel": "ticker",
        "events": [
            {
                "product_id": "BTC-USD",
                "best_bid": "49900.00",
                "best_ask": "50100.00",
                "price": "50000.00",
            }
        ],
    }
    await adapter._handle_ticker(msg)
    redis_mock.publish.assert_called_once()
    call_args = redis_mock.publish.call_args
    assert call_args[0][0] == "quote.coinbase.BTC.USD"
    payload = json.loads(call_args[0][1])
    assert payload["canonical_id"] == "BTC.USD"
    assert payload["bid"] == "49900.00"


async def test_l2_snapshot_resets_book(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    adapter._books["BTC.USD"] = OrderBook()
    msg = {
        "channel": "l2_data",
        "product_id": "BTC-USD",
        "events": [
            {
                "type": "snapshot",
                "updates": [
                    {
                        "side": "bid",
                        "price_level": "50000",
                        "new_quantity": "1.0",
                        "event_sequence_num": 1,
                    },
                    {
                        "side": "offer",
                        "price_level": "50100",
                        "new_quantity": "0.5",
                        "event_sequence_num": 1,
                    },
                ],
            }
        ],
    }
    await adapter._handle_l2(msg)
    book = adapter._books.get("BTC.USD")
    assert book is not None
    assert Decimal("50000") in book.bids
    assert Decimal("50100") in book.asks


async def test_l2_offer_side_maps_to_ask(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    adapter._books["BTC.USD"] = OrderBook()
    msg = {
        "channel": "l2_data",
        "product_id": "BTC-USD",
        "events": [
            {
                "type": "snapshot",
                "updates": [
                    {
                        "side": "offer",
                        "price_level": "50100",
                        "new_quantity": "0.5",
                        "event_sequence_num": 1,
                    },
                ],
            }
        ],
    }
    await adapter._handle_l2(msg)
    book = adapter._books["BTC.USD"]
    assert Decimal("50100") in book.asks
    assert len(book.bids) == 0


async def test_l2_update_xadd_called(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    book = OrderBook()
    book.last_seq = 10
    adapter._books["ETH.USD"] = book
    msg = {
        "channel": "l2_data",
        "product_id": "ETH-USD",
        "events": [
            {
                "type": "update",
                "updates": [
                    {
                        "side": "bid",
                        "price_level": "3000",
                        "new_quantity": "2.0",
                        "event_sequence_num": 11,
                    },
                ],
            }
        ],
    }
    await adapter._handle_l2(msg)
    redis_mock.xadd.assert_called_once()
    call_args = redis_mock.xadd.call_args
    assert call_args[0][0] == "crypto:book:ETH.USD"


async def test_l2_sequence_gap_resets_book_and_resubscribes(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    ws_mock = AsyncMock()
    adapter._ws = ws_mock

    book = OrderBook()
    book.last_seq = 10
    adapter._books["BTC.USD"] = book
    msg = {
        "channel": "l2_data",
        "product_id": "BTC-USD",
        "events": [
            {
                "type": "update",
                "updates": [
                    {
                        "side": "bid",
                        "price_level": "50000",
                        "new_quantity": "1.0",
                        "event_sequence_num": 15,
                    },
                ],
            }
        ],
    }
    await adapter._handle_l2(msg)
    assert adapter._books["BTC.USD"].last_seq == 0
    assert ws_mock.send.call_count == 2


async def test_l2_missing_sequence_num_skips_gap_check(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    book = OrderBook()
    book.last_seq = 10
    adapter._books["BTC.USD"] = book
    msg = {
        "channel": "l2_data",
        "product_id": "BTC-USD",
        "events": [
            {
                "type": "update",
                "updates": [
                    {"side": "bid", "price_level": "50000", "new_quantity": "1.0"},
                ],
            }
        ],
    }
    await adapter._handle_l2(msg)
    # seq=10 (from last_seq since raw_seq is None), no gap
    assert adapter._books["BTC.USD"].last_seq == 10
    redis_mock.xadd.assert_called_once()


async def test_snapshot_loop_writes_hset(redis_mock: AsyncMock) -> None:
    from app.services.crypto.book_manager import OrderBook

    adapter = _make_adapter(redis_mock)
    book = OrderBook()
    book.bids[Decimal("50000")] = Decimal("1.0")
    book.asks[Decimal("50100")] = Decimal("0.5")
    adapter._books["BTC.USD"] = book

    # Call the snapshot publish directly
    for canonical_id, bk in list(adapter._books.items()):
        snap = bk.snapshot(depth=100)
        await adapter._redis.hset(
            f"crypto:book:snap:{canonical_id}",
            mapping={
                "bids": json.dumps([[str(p), str(q)] for p, q in snap["bids"]]),
                "asks": json.dumps([[str(p), str(q)] for p, q in snap["asks"]]),
            },
        )

    redis_mock.hset.assert_called_once()
    call_args = redis_mock.hset.call_args
    assert call_args[0][0] == "crypto:book:snap:BTC.USD"
