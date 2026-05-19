import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.bot.bar_aggregator import BarAggregator
from app.bot.base import BarEvent


def make_tick(canonical_id: str, price: float, ts: datetime, volume: float = 100.0) -> dict:
    return {
        "canonical_id": canonical_id,
        "price": str(price),
        "volume": str(volume),
        "ts": ts.isoformat(),
    }


@pytest.mark.asyncio
async def test_minute_bar_boundary():
    """Ticks before and after the 1-minute boundary produce one complete bar."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    agg = BarAggregator(
        canonical_id="AAPL",
        timeframe="1m",
        queue=queue,
        bot_id="test",
    )
    agg.unpause()

    t0 = datetime(2026, 1, 2, 10, 0, 30, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, 10, 1, 5, tzinfo=UTC)

    await agg.process_tick(make_tick("AAPL", 150.0, t0))
    await agg.process_tick(make_tick("AAPL", 151.0, t0.replace(second=55)))
    await agg.process_tick(make_tick("AAPL", 149.0, t1))

    bar: BarEvent = queue.get_nowait()
    assert bar.open == Decimal("150.0")
    assert bar.high == Decimal("151.0")
    assert bar.close == Decimal("151.0")
    assert bar.timeframe == "1m"


@pytest.mark.asyncio
async def test_late_tick_dropped():
    """Ticks arriving >2s after their bar boundary are dropped."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    agg = BarAggregator(canonical_id="AAPL", timeframe="1m", queue=queue, bot_id="test")
    agg.unpause()

    t0 = datetime(2026, 1, 2, 10, 0, 30, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 150.0, t0))

    t1 = datetime(2026, 1, 2, 10, 1, 5, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 151.0, t1))

    late = datetime(2026, 1, 2, 10, 0, 55, tzinfo=UTC)
    agg._now_override = datetime(2026, 1, 2, 10, 1, 10, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 999.0, late))

    bar: BarEvent = queue.get_nowait()
    assert bar.close != Decimal("999.0")
    assert queue.empty()


@pytest.mark.asyncio
async def test_paused_delivery_accumulates_ticks():
    """When paused, ticks accumulate but no bar events are emitted."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    agg = BarAggregator(canonical_id="AAPL", timeframe="1m", queue=queue, bot_id="test")

    t0 = datetime(2026, 1, 2, 10, 0, 30, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, 10, 1, 5, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 150.0, t0))
    await agg.process_tick(make_tick("AAPL", 151.0, t1))

    assert queue.empty()

    agg.unpause()
    t2 = datetime(2026, 1, 2, 10, 2, 5, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 152.0, t2))

    assert queue.empty()


@pytest.mark.asyncio
async def test_queue_overflow_drops_oldest():
    """Queue full: oldest bar dropped."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    agg = BarAggregator(canonical_id="AAPL", timeframe="1m", queue=queue, bot_id="test-bot")
    agg.unpause()

    for _ in range(2):
        queue.put_nowait(MagicMock())

    t0 = datetime(2026, 1, 2, 10, 0, 30, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, 10, 1, 5, tzinfo=UTC)
    await agg.process_tick(make_tick("AAPL", 150.0, t0))
    await agg.process_tick(make_tick("AAPL", 151.0, t1))

    assert queue.qsize() == 2
