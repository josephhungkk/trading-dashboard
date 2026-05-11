from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.services.volatility_service import VolatilityService


@pytest.mark.asyncio
async def test_returns_none_when_insufficient_bars() -> None:
    """Less than 15 closes in bars_1d → service returns None (caller raises 422)."""
    instrument_id = 12345
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    rows = [
        (date(2026, 5, 1 + i), Decimal("100"), Decimal("100"), Decimal("100")) for i in range(14)
    ]
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    factory = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=session),
            __aexit__=AsyncMock(),
        )
    )

    svc = VolatilityService(db_factory=factory, redis=redis)
    result = await svc.compute(instrument_id=instrument_id, asof_date=date(2026, 5, 14))

    assert result is None
