"""Phase 10a.5 A2: PnlIntradayWriter — per-account-per-day intraday PnL upsert.

Source-field invariant: realized_today MUST come from
SUM(positions[*].realized_pnl_today) (proto Position field 7), NEVER from
Summary.realized_pnl (proto Summary field 3 — cumulative since open for IBKR;
would invert the max-daily-loss gate).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


class PnlIntradayWriter:
    """Upsert + prune for the ``pnl_intraday`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        account_id: uuid.UUID,
        realized_today: Decimal,
        unrealized: Decimal,
        currency: str,
        summary_updated_at: datetime,
        source_label: str,
    ) -> None:
        """INSERT … ON CONFLICT DO UPDATE with summary_updated_at guard.

        The UPDATE only fires when the new summary_updated_at is at least as
        fresh as the stored one AND the (realized_today, unrealized) tuple
        actually changed — eliminates dead-row churn (MED-1, MED-6).
        """
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        sql = (
            "INSERT INTO pnl_intraday ("
            "  account_id, day_start_utc, realized_today, unrealized,"
            "  currency, summary_updated_at, source_label"
            ") VALUES (:aid, :day, :r, :u, :c, :sua, :sl)"
            " ON CONFLICT (account_id, day_start_utc) DO UPDATE"
            "    SET realized_today     = EXCLUDED.realized_today,"
            "        unrealized         = EXCLUDED.unrealized,"
            "        currency           = EXCLUDED.currency,"
            "        summary_updated_at = EXCLUDED.summary_updated_at,"
            "        source_label       = EXCLUDED.source_label,"
            "        updated_at         = now()"
            "  WHERE EXCLUDED.summary_updated_at >= pnl_intraday.summary_updated_at"
            "    AND (pnl_intraday.realized_today, pnl_intraday.unrealized)"
            "        IS DISTINCT FROM (EXCLUDED.realized_today, EXCLUDED.unrealized)"
        )

        params = {
            "aid": account_id,
            "day": day_start,
            "r": realized_today,
            "u": unrealized,
            "c": currency,
            "sua": summary_updated_at,
            "sl": source_label,
        }

        await self._session.execute(text(sql), params)

    async def prune_older_than(self, *, days: int) -> int:
        """Delete pnl_intraday rows older than ``days`` days.

        Returns the number of rows deleted.
        """
        sql = (
            "DELETE FROM pnl_intraday"
            " WHERE day_start_utc < (now() AT TIME ZONE 'UTC') - make_interval(days => :d)"
        )

        result = await self._session.execute(text(sql), {"d": days})
        # mypy: AsyncSession.execute returns Result[Any]; rowcount lives on the
        # underlying CursorResult for DML statements. Real return is int|None.
        return int(getattr(result, "rowcount", 0) or 0)
