from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from app.core import metrics

log = structlog.get_logger()


class InstrumentLinker:
    """Resolves a filing's ticker/CIK to canonical_id + instrument_id."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def link(
        self,
        *,
        source: str,
        ticker: str | None = None,
        cik: str | None = None,
    ) -> tuple[int | None, str | None]:
        """Return (instrument_id, canonical_id). Both may be None on failure."""
        if ticker:
            row = await self._db.execute(
                sa.text(
                    "SELECT i.id, i.canonical_id FROM instruments i "
                    "JOIN symbol_aliases sa ON sa.instrument_id = i.id "
                    "WHERE sa.alias = :ticker LIMIT 1"
                ),
                {"ticker": ticker},
            )
            r = row.fetchone()
            if r:
                return r.id, r.canonical_id

        if cik:
            row = await self._db.execute(
                sa.text(
                    "SELECT i.id, i.canonical_id FROM instruments i "
                    "JOIN symbol_aliases sa ON sa.instrument_id = i.id "
                    "WHERE sa.alias = :cik LIMIT 1"
                ),
                {"cik": cik},
            )
            r = row.fetchone()
            if r:
                return r.id, r.canonical_id

        metrics.filings_instrument_link_failures_total.labels(source=source).inc()
        return None, ticker or cik or None
