from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.services.volatility_service import VolatilityService
from tests.fixtures.bars_1d_factory import (
    GOLDEN_AAPL_ATR14,
    GOLDEN_AAPL_CLOSES,
    GOLDEN_AAPL_START_DATE,
    GOLDEN_AAPL_VOL14_ANNUALIZED,
    build_bars_1d_rows,
)


def _factory_returning(rows: list) -> MagicMock:
    """Build a db_factory mock whose async context yields a session that
    returns ``rows`` from ``execute().all()``."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=session),
            __aexit__=AsyncMock(),
        )
    )


@pytest.mark.asyncio
async def test_returns_none_when_insufficient_bars() -> None:
    """Less than 15 closes in bars_1d → service returns None (caller raises 422)."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    rows = [
        (date(2026, 5, 1 + i), Decimal("100"), Decimal("100"), Decimal("100")) for i in range(14)
    ]
    svc = VolatilityService(db_factory=_factory_returning(rows), redis=redis)
    result = await svc.compute(instrument_id=12345, asof_date=date(2026, 5, 14))
    assert result is None


@pytest.mark.asyncio
async def test_compute_returns_golden_values() -> None:
    """The pinned AAPL closes produce the offline-computed golden vol + ATR.

    ``bars_1d`` is a TimescaleDB continuous aggregate (read-only), so this
    test injects pre-built rows via the db_factory mock — the math, not the
    SQL, is what the golden values pin.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    # Service expects rows in oldest..newest order; service code does an
    # internal reverse(), so the mock should return newest-first to match
    # the real SQL's ``ORDER BY bar_date DESC``.
    rows_oldest_first = build_bars_1d_rows(GOLDEN_AAPL_CLOSES, GOLDEN_AAPL_START_DATE)
    rows_newest_first = list(reversed(rows_oldest_first))

    svc = VolatilityService(db_factory=_factory_returning(rows_newest_first), redis=redis)
    asof = GOLDEN_AAPL_START_DATE + timedelta(days=14)
    result = await svc.compute(instrument_id=12345, asof_date=asof)

    assert result is not None
    assert abs(result.realized_vol14_annualized - GOLDEN_AAPL_VOL14_ANNUALIZED) < Decimal("1e-6")
    assert abs(result.atr14 - GOLDEN_AAPL_ATR14) < Decimal("1e-6")
    assert result.bars_used == 14
    assert result.asof_date == asof
