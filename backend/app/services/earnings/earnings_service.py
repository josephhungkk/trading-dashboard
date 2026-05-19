from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import text

from app.core import metrics
from app.services.earnings.finnhub_calendar import FinnhubCalendarPoller
from app.services.earnings.nasdaq_calendar import NasdaqCalendarPoller

log = structlog.get_logger()


class EarningsService:
    def __init__(self, *, db_factory: Any, redis: Any, finnhub_api_key: str | None = None) -> None:
        self.db_factory = db_factory
        self.redis = redis
        self.finnhub_api_key = finnhub_api_key

    async def poll_nasdaq(self) -> None:
        rows = await NasdaqCalendarPoller().fetch()
        async with self.db_factory() as db:
            for row in rows:
                await self._upsert_event(db, row)
            await db.commit()

    async def poll_finnhub(self) -> None:
        rows = await FinnhubCalendarPoller(api_key=self.finnhub_api_key).fetch()
        async with self.db_factory() as db:
            for row in rows:
                await self._upsert_event(db, row)
            await db.commit()

    async def _resolve_instrument(self, db: Any, ticker: str) -> tuple[int, str] | None:
        result = await db.execute(
            text(
                """
                SELECT i.id, i.canonical_id
                  FROM symbol_aliases sa
                  JOIN instruments i ON i.id = sa.instrument_id
                 WHERE upper(sa.raw_symbol) = upper(:ticker)
                 ORDER BY CASE sa.source
                            WHEN 'nasdaq_api' THEN 0
                            WHEN 'finnhub_api' THEN 1
                            ELSE 2
                          END
                 LIMIT 1
                """
            ),
            {"ticker": ticker},
        )
        row = result.mappings().one_or_none()
        if row is not None:
            return int(row["id"]), str(row["canonical_id"])

        fallback = await db.execute(
            text(
                """
                SELECT id, canonical_id
                  FROM instruments
                 WHERE upper(canonical_id) LIKE upper(:prefix)
                 ORDER BY id
                 LIMIT 1
                """
            ),
            {"prefix": f"%:{ticker}:%"},
        )
        fallback_row = fallback.mappings().one_or_none()
        if fallback_row is None:
            return None
        return int(fallback_row["id"]), str(fallback_row["canonical_id"])

    def _parse_date(self, value: Any) -> date | None:
        if isinstance(value, date):
            return value
        if not value:
            return None
        return date.fromisoformat(str(value)[:10])

    def _parse_decimal(self, value: Any) -> Decimal | None:
        if value in (None, "", "N/A", "--"):
            return None
        try:
            return Decimal(str(value).replace("$", "").replace(",", ""))
        except InvalidOperation:
            return None
        except ValueError:
            return None

    async def _upsert_event(self, db: Any, row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").strip()
        announced_date = self._parse_date(row.get("announced_date"))
        if not ticker or announced_date is None:
            return
        instrument = await self._resolve_instrument(db, ticker)
        if instrument is None:
            log.info("earnings_instrument_unresolved", ticker=ticker, source=row.get("source"))
            return
        instrument_id, canonical_id = instrument
        result = await db.execute(
            text(
                """
                INSERT INTO earnings_events (
                    instrument_id,
                    canonical_id,
                    announced_date,
                    time_of_day,
                    eps_estimate,
                    eps_actual,
                    revenue_estimate,
                    revenue_actual,
                    source,
                    source_priority,
                    confirmed,
                    updated_at
                )
                VALUES (
                    :instrument_id,
                    :canonical_id,
                    :announced_date,
                    :time_of_day,
                    :eps_estimate,
                    :eps_actual,
                    :revenue_estimate,
                    :revenue_actual,
                    :source,
                    :source_priority,
                    :confirmed,
                    now()
                )
                ON CONFLICT (instrument_id, announced_date) DO UPDATE
                   SET canonical_id = EXCLUDED.canonical_id,
                       time_of_day = EXCLUDED.time_of_day,
                       eps_estimate = COALESCE(EXCLUDED.eps_estimate, earnings_events.eps_estimate),
                       eps_actual = COALESCE(EXCLUDED.eps_actual, earnings_events.eps_actual),
                       revenue_estimate = COALESCE(
                           EXCLUDED.revenue_estimate,
                           earnings_events.revenue_estimate
                       ),
                       revenue_actual = COALESCE(
                           EXCLUDED.revenue_actual,
                           earnings_events.revenue_actual
                       ),
                       source = EXCLUDED.source,
                       source_priority = EXCLUDED.source_priority,
                       confirmed = earnings_events.confirmed OR EXCLUDED.confirmed,
                       updated_at = now()
                 WHERE EXCLUDED.source_priority >= earnings_events.source_priority
                RETURNING id
                """
            ),
            {
                "instrument_id": instrument_id,
                "canonical_id": canonical_id,
                "announced_date": announced_date,
                "time_of_day": row.get("time_of_day") or "unknown",
                "eps_estimate": self._parse_decimal(row.get("eps_estimate")),
                "eps_actual": self._parse_decimal(row.get("eps_actual")),
                "revenue_estimate": self._parse_decimal(row.get("revenue_estimate")),
                "revenue_actual": self._parse_decimal(row.get("revenue_actual")),
                "source": row.get("source"),
                "source_priority": int(row.get("source_priority") or 0),
                "confirmed": bool(row.get("confirmed") or False),
            },
        )
        if result.scalar_one_or_none() is None:
            metrics.earnings_dedup_skips_total.labels(source=str(row.get("source"))).inc()
