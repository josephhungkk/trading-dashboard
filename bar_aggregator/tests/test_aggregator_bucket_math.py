from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bar_aggregator.app.aggregator import AggregatorEngine, BucketState

pytestmark = [pytest.mark.unit]


def test_bucket_open_high_low_close_on_3_ticks() -> None:
    bucket = BucketState(bucket_start=datetime(2026, 5, 7, 15, 30, tzinfo=timezone.utc))

    bucket.apply_tick(Decimal("100.10"), Decimal("5"))
    bucket.apply_tick(Decimal("100.50"), Decimal("3"))
    bucket.apply_tick(Decimal("99.80"), Decimal("2"))

    assert bucket.open == Decimal("100.10")
    assert bucket.high == Decimal("100.50")
    assert bucket.low == Decimal("99.80")
    assert bucket.close == Decimal("99.80")
    assert bucket.volume == Decimal("10")
    assert bucket.trade_count == 3
    assert bucket.volume_source == "tape"


def test_bucket_quote_proxy_volume_when_no_trade_size() -> None:
    bucket = BucketState(bucket_start=datetime(2026, 5, 7, 15, 30, tzinfo=timezone.utc))

    bucket.apply_quote(bid=Decimal("100.0"), ask=Decimal("100.1"))

    assert bucket.open == Decimal("100.05")
    assert bucket.close == Decimal("100.05")
    assert bucket.volume_source == "quote_proxy"
    assert bucket.trade_count == 0
    assert bucket.volume is None


def test_tape_overrides_quote_proxy() -> None:
    bucket = BucketState(bucket_start=datetime(2026, 5, 7, 15, 30, tzinfo=timezone.utc))

    bucket.apply_quote(bid=Decimal("100.0"), ask=Decimal("100.1"))
    bucket.apply_tick(price=Decimal("100.2"), volume=Decimal("7"))

    assert bucket.volume_source == "tape"
    assert bucket.volume == Decimal("7")


def test_engine_routes_tick_to_correct_bucket() -> None:
    engine = AggregatorEngine()
    ts = datetime(2026, 5, 7, 15, 30, 0, 250_000, tzinfo=timezone.utc)
    bucket_start = datetime(2026, 5, 7, 15, 30, 0, tzinfo=timezone.utc)

    engine.on_tick(
        instrument_id=42,
        source="schwab",
        ts=ts,
        price=Decimal("100"),
        volume=Decimal("1"),
    )

    bucket = engine.peek_bucket(42, bucket_start)
    assert bucket is not None
    assert bucket.close == Decimal("100")


def test_engine_isolates_per_source() -> None:
    engine = AggregatorEngine()
    ts = datetime(2026, 5, 7, 15, 30, tzinfo=timezone.utc)

    engine.on_tick(
        instrument_id=42,
        source="schwab",
        ts=ts,
        price=Decimal("100"),
        volume=Decimal("1"),
    )
    engine.on_tick(
        instrument_id=42,
        source="ibkr",
        ts=ts,
        price=Decimal("101"),
        volume=Decimal("2"),
    )

    assert engine.buckets[(42, "schwab")][ts].close == Decimal("100")
    assert engine.buckets[(42, "schwab")][ts].volume == Decimal("1")
    assert engine.buckets[(42, "ibkr")][ts].close == Decimal("101")
    assert engine.buckets[(42, "ibkr")][ts].volume == Decimal("2")
