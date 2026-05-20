from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cgt import engine
from app.services.cgt.types import TaxEvent


def _te(cgt_track: str) -> TaxEvent:
    return TaxEvent(
        account_id=uuid.uuid4(),
        instrument_id=1,
        cgt_track=cgt_track,
        event_type="fill",
        side="buy",
        qty=Decimal("1"),
        price_gbp=Decimal("10"),
        fx_rate=Decimal("1"),
        fx_source="none",
        original_currency="GBP",
        executed_at=datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC),
    )


def _make_begin_nested() -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture
def mock_session():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.begin_nested = MagicMock(side_effect=lambda: _make_begin_nested())
    return session


@pytest.mark.asyncio
async def test_exempt_track_no_engine_call(mock_session):
    te = _te("exempt")
    with patch("app.services.cgt.pool_engine.process") as mock_pool:
        await engine.process(te, mock_session)
        mock_pool.assert_not_called()


@pytest.mark.asyncio
async def test_pool_track_dispatches_pool_engine(mock_session):
    te = _te("pool")
    with patch("app.services.cgt.pool_engine.process", new_callable=AsyncMock) as mock_pool:
        await engine.process(te, mock_session)
        mock_pool.assert_called_once()


@pytest.mark.asyncio
async def test_derivative_track_dispatches_derivative_engine(mock_session):
    te = _te("derivative")
    with patch("app.services.cgt.derivative_engine.process", new_callable=AsyncMock) as mock_deriv:
        await engine.process(te, mock_session)
        mock_deriv.assert_called_once()
