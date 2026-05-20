from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cgt import derivative_engine
from app.services.cgt.types import TaxEvent


def _te(*, side, qty, price_gbp, executed_at, account_id=None, instrument_id=2):
    return TaxEvent(
        account_id=account_id or uuid.uuid4(),
        instrument_id=instrument_id,
        cgt_track="derivative",
        event_type="fill",
        side=side,
        qty=Decimal(qty),
        price_gbp=Decimal(price_gbp),
        fx_rate=Decimal("1"),
        fx_source="none",
        original_currency="GBP",
        executed_at=executed_at,
    )


def _utc(y, m, d):
    return datetime(y, m, d, 12, 0, 0, tzinfo=UTC)


def _make_begin_nested() -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture
def mock_session():
    derivative_engine._TEST_DISPOSALS.clear()
    derivative_engine._TEST_POSITIONS.clear()
    session = MagicMock()
    session.begin_nested = MagicMock(side_effect=lambda: _make_begin_nested())
    empty_result = MagicMock()
    empty_result.fetchone = lambda: None
    session.execute = AsyncMock(return_value=empty_result)
    return session


@pytest.mark.asyncio
async def test_long_open_close_gain(mock_session):
    """Long future: buy @ 100, sell @ 120 → gain = 20."""
    account_id = uuid.uuid4()
    open_te = _te(
        side="buy", qty="1", price_gbp="100", executed_at=_utc(2025, 3, 1), account_id=account_id
    )
    close_te = _te(
        side="sell", qty="1", price_gbp="120", executed_at=_utc(2025, 3, 15), account_id=account_id
    )

    # First call: no open position → opens it
    await derivative_engine.process(open_te, mock_session)
    assert len(derivative_engine._TEST_POSITIONS) == 1

    # Second call: open position returned → closes it
    open_pos = MagicMock()
    open_pos.id = uuid.uuid4()
    open_pos.side = "long"
    open_pos.qty = Decimal("1")
    open_pos.total_proceeds_gbp = Decimal("0")
    open_pos.total_cost_gbp = Decimal("100")

    close_result = MagicMock()
    close_result.fetchone = lambda: open_pos
    mock_session.execute = AsyncMock(return_value=close_result)

    await derivative_engine.process(close_te, mock_session)

    disposals = derivative_engine._test_get_disposals()
    assert len(disposals) == 1
    assert disposals[0].gain_gbp == Decimal("20")
    assert disposals[0].match_type == "derivative"


@pytest.mark.asyncio
async def test_short_open_close_gain(mock_session):
    """Short future: sell @ 100, buy @ 80 → gain = 20."""
    account_id = uuid.uuid4()
    open_te = _te(
        side="sell", qty="1", price_gbp="100", executed_at=_utc(2025, 4, 1), account_id=account_id
    )
    close_te = _te(
        side="buy", qty="1", price_gbp="80", executed_at=_utc(2025, 4, 15), account_id=account_id
    )

    await derivative_engine.process(open_te, mock_session)

    open_pos = MagicMock()
    open_pos.id = uuid.uuid4()
    open_pos.side = "short"
    open_pos.qty = Decimal("1")
    open_pos.total_proceeds_gbp = Decimal("100")
    open_pos.total_cost_gbp = Decimal("0")

    close_result = MagicMock()
    close_result.fetchone = lambda: open_pos
    mock_session.execute = AsyncMock(return_value=close_result)

    await derivative_engine.process(close_te, mock_session)

    disposals = derivative_engine._test_get_disposals()
    assert any(d.gain_gbp == Decimal("20") for d in disposals)
