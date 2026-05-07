"""BarService orchestrator skeleton — full impl lands in Chunk D."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final, NamedTuple

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_SOURCE_PRIORITY: Final[Mapping[str, int]] = {
    "schwab": 1,
    "alpaca": 2,
    "ibkr": 3,
    "futu": 4,
    "aggregator-schwab": 99,
    "aggregator-alpaca": 99,
    "aggregator-ibkr": 99,
    "aggregator-futu": 99,
}


def _priority_for_source(source: str) -> int:
    """Single chokepoint mapping source → source_priority for UPSERT WHERE clause."""
    if source not in _SOURCE_PRIORITY:
        raise ValueError(f"unknown bar source: {source!r}")
    return _SOURCE_PRIORITY[source]


class ActiveSetRow(NamedTuple):
    """Row from BarService.active_set(): one entry per active instrument."""

    instrument_id: int
    recency_score: int


class BarService:
    """Orchestrator skeleton; full impl arrives in Chunk D."""

    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            logger.info("bar_service.start")
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            logger.info("bar_service.stop")
            self._started = False

    async def active_set(self, session: AsyncSession) -> list[ActiveSetRow]:
        """Return up to 1000 instruments worth pre-warming.

        Active set = (positions UNION watchlist_entries UNION recent chart_layouts),
        deduped, ordered by recency_score DESC, capped at 1000 (matches the
        per-aggregator memory cap; sharding takes over above this — see spec
        §3 lines 396-417 + §4 line 509).
        """
        rows = (
            await session.execute(
                text(
                    """
                    WITH cfg AS (
                      SELECT value::int AS recency_days
                      FROM app_config
                      WHERE namespace = 'charts'
                        AND key = 'bar_active_set_recency_days'
                    )
                    SELECT instrument_id, MAX(recency_score) AS recency_score
                    FROM (
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM NOW())::bigint AS recency_score
                        FROM positions
                       WHERE instrument_id IS NOT NULL
                      UNION ALL
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM NOW())::bigint
                        FROM watchlist_entries
                       WHERE instrument_id IS NOT NULL
                      UNION ALL
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM updated_at)::bigint
                        FROM chart_layouts
                       WHERE updated_at >
                             NOW() - (SELECT recency_days FROM cfg) * INTERVAL '1 day'
                    ) sources
                    GROUP BY instrument_id
                    ORDER BY recency_score DESC
                    LIMIT 1000
                    """
                )
            )
        ).all()
        result = [
            ActiveSetRow(instrument_id=r.instrument_id, recency_score=r.recency_score) for r in rows
        ]
        logger.info("bar_service.active_set", count=len(result))
        return result
