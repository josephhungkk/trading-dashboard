from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.backtest.bar_feed import BarFeed

UTC = UTC


def make_bar_row(ts: datetime, open_: float = 100.0) -> dict:
    return {
        "bucket_start": ts,
        "open": Decimal(str(open_)),
        "high": Decimal(str(open_ + 1)),
        "low": Decimal(str(open_ - 1)),
        "close": Decimal(str(open_)),
        "volume": Decimal("1000"),
    }


@pytest.mark.asyncio
async def test_db_bars_returned_sorted(db_session):
    feed = BarFeed(db=db_session, redis=None)
    with patch.object(feed, "_fetch_db_bars", new_callable=AsyncMock) as mock_fetch:
        ts1 = datetime(2024, 1, 2, tzinfo=UTC)
        ts2 = datetime(2024, 1, 3, tzinfo=UTC)
        mock_fetch.return_value = [make_bar_row(ts2), make_bar_row(ts1)]
        bars = await feed.load(
            canonical_id="AAPL",
            timeframe="1d",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            bars_source="db",
            instrument_id=1,
        )
    assert bars[0].ts < bars[1].ts


@pytest.mark.asyncio
async def test_csv_bar_overrides_db_bar(db_session):
    feed = BarFeed(db=db_session, redis=None)
    ts = datetime(2024, 1, 2, tzinfo=UTC)
    with (
        patch.object(feed, "_fetch_db_bars", new_callable=AsyncMock) as mock_db,
        patch.object(feed, "_fetch_csv_bars", new_callable=AsyncMock) as mock_csv,
    ):
        mock_db.return_value = [make_bar_row(ts, open_=100.0)]
        mock_csv.return_value = [make_bar_row(ts, open_=999.0)]
        bars = await feed.load(
            canonical_id="AAPL",
            timeframe="1d",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            bars_source="csv",
            instrument_id=1,
            upload_id="some-uuid",
        )
    # CSV wins on collision
    assert bars[0].open == Decimal("999.0")


@pytest.mark.asyncio
async def test_csv_bars_outside_range_ignored(db_session):
    feed = BarFeed(db=db_session, redis=None)
    ts_in = datetime(2024, 1, 2, tzinfo=UTC)
    ts_out = datetime(2025, 6, 1, tzinfo=UTC)
    with (
        patch.object(feed, "_fetch_db_bars", new_callable=AsyncMock) as mock_db,
        patch.object(feed, "_fetch_csv_bars", new_callable=AsyncMock) as mock_csv,
    ):
        mock_db.return_value = [make_bar_row(ts_in)]
        mock_csv.return_value = [make_bar_row(ts_out, open_=777.0)]
        bars = await feed.load(
            canonical_id="AAPL",
            timeframe="1d",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            bars_source="csv",
            instrument_id=1,
            upload_id="some-uuid",
        )
    assert all(b.open != Decimal("777.0") for b in bars)
