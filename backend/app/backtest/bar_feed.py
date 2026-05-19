from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.base import BarEvent

logger = structlog.get_logger(__name__)
UTC = UTC

_TF_TO_TABLE = {
    "1m": "bars_1m",
    "5m": "bars_5m",
    "15m": "bars_15m",
    "30m": "bars_30m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}


class BarFeedError(Exception):
    pass


class BarFeed:
    def __init__(self, *, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    async def load(
        self,
        *,
        canonical_id: str,
        timeframe: str,
        start_date: date,
        end_date: date,
        bars_source: str,
        instrument_id: int,
        upload_id: str | None = None,
    ) -> list[BarEvent]:
        db_rows = await self._fetch_db_bars(instrument_id, timeframe, start_date, end_date)
        merged: dict[datetime, BarEvent] = {
            r["bucket_start"].replace(tzinfo=UTC): self._row_to_event(canonical_id, timeframe, r)
            for r in db_rows
        }

        if bars_source == "csv" and upload_id:
            csv_rows = await self._fetch_csv_bars(upload_id, instrument_id, start_date, end_date)
            for r in csv_rows:
                ts = r["bucket_start"].replace(tzinfo=UTC)
                if date(ts.year, ts.month, ts.day) < start_date:
                    continue
                if date(ts.year, ts.month, ts.day) > end_date:
                    continue
                merged[ts] = self._row_to_event(canonical_id, timeframe, r)

        return sorted(merged.values(), key=lambda b: b.ts)

    async def _fetch_db_bars(
        self, instrument_id: int, timeframe: str, start_date: date, end_date: date
    ) -> list[dict]:
        table = _TF_TO_TABLE.get(timeframe, "bars_1m")
        result = await self._db.execute(
            text(f"""
                SELECT bucket_start, open, high, low, close, volume
                FROM {table}
                WHERE instrument_id = :iid
                  AND bucket_start >= :start AND bucket_start < :end
                ORDER BY bucket_start
            """),
            {"iid": instrument_id, "start": start_date, "end": end_date},
        )
        return [dict(r._mapping) for r in result]

    async def _fetch_csv_bars(
        self, upload_id: str, instrument_id: int, start_date: date, end_date: date
    ) -> list[dict]:
        result = await self._db.execute(
            text("""
                SELECT bucket_start, open, high, low, close, volume
                FROM backtest_bars
                WHERE upload_id = :uid AND instrument_id = :iid
                  AND bucket_start >= :start AND bucket_start < :end
                ORDER BY bucket_start
            """),
            {"uid": upload_id, "iid": instrument_id, "start": start_date, "end": end_date},
        )
        return [dict(r._mapping) for r in result]

    @staticmethod
    def _row_to_event(canonical_id: str, timeframe: str, row: dict) -> BarEvent:
        ts = row["bucket_start"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return BarEvent(
            canonical_id=canonical_id,
            timeframe=timeframe,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])) if row.get("volume") is not None else Decimal("0"),
            ts=ts,
        )
