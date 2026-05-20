"""Golden tests for FX chokepoint. All callers MUST use fx.to_gbp()."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cgt.fx import FxRateNotFoundError, to_gbp


def _utc(y, m, d, h=12):
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_gbp_passthrough():
    session = MagicMock()
    gbp, rate, src = await to_gbp(Decimal("100"), "GBP", _utc(2025, 7, 1), session)
    assert gbp == Decimal("100")
    assert rate == Decimal("1")
    assert src == "none"


@pytest.mark.asyncio
async def test_gbx_to_gbp():
    """1234p / 100 = £12.34"""
    session = MagicMock()
    gbp, rate, src = await to_gbp(Decimal("1234"), "GBX", _utc(2025, 7, 1), session)
    assert gbp == Decimal("12.34")
    assert rate == Decimal("100")
    assert src == "gbx_to_gbp"


@pytest.mark.asyncio
async def test_usd_hmrc_monthly(mock_hmrc_rate_session):
    """$1000 / 1.27 = £787.40..."""
    gbp, rate, src = await to_gbp(Decimal("1000"), "USD", _utc(2025, 1, 15), mock_hmrc_rate_session)
    assert rate == Decimal("1.27")
    assert src == "hmrc_monthly"
    assert abs(gbp - Decimal("787.40")) < Decimal("0.01")


@pytest.mark.asyncio
async def test_usd_fallback_prev_pending(mock_prev_only_session):
    """When current month rate missing, use previous month and mark pending."""
    _, _, src = await to_gbp(Decimal("1000"), "USD", _utc(2025, 2, 1), mock_prev_only_session)
    assert src == "hmrc_monthly_prev_pending"


@pytest.mark.asyncio
async def test_no_rate_raises(mock_empty_session):
    with pytest.raises(FxRateNotFoundError):
        await to_gbp(Decimal("1000"), "EUR", _utc(2025, 3, 1), mock_empty_session)


@pytest.fixture
def mock_hmrc_rate_session():
    """Session that returns rate 1.27 for USD 2025-01."""
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = Decimal("1.27")
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture
def mock_prev_only_session():
    """First query (current month) returns None; second (prev month) returns 1.28."""
    session = MagicMock()
    results = [MagicMock(), MagicMock()]
    results[0].scalar_one_or_none.return_value = None
    results[1].scalar_one_or_none.return_value = Decimal("1.28")
    session.execute = AsyncMock(side_effect=results)
    return session


@pytest.fixture
def mock_empty_session():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    return session
