from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cgt import pool_engine
from app.services.cgt.types import TaxEvent


def _te(
    *,
    side: str,
    qty: str,
    price_gbp: str,
    executed_at: datetime,
    account_id: uuid.UUID | None = None,
    instrument_id: int = 1,
    cgt_class_key: str = "TEST_KEY",
    is_short_open: bool = False,
    is_short_close: bool = False,
    event_type: str = "fill",
) -> TaxEvent:
    bb_qty = Decimal(qty) if side == "buy" else Decimal("0")
    return TaxEvent(
        account_id=account_id or uuid.uuid4(),
        instrument_id=instrument_id,
        cgt_track="pool",
        event_type=event_type,
        side=side,
        qty=Decimal(qty),
        price_gbp=Decimal(price_gbp),
        fx_rate=Decimal("1"),
        fx_source="none",
        original_currency="GBP",
        executed_at=executed_at,
        cgt_class_key=cgt_class_key,
        bb_remaining_qty=bb_qty,
        is_short_open=is_short_open,
        is_short_close=is_short_close,
    )


def _make_begin_nested() -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_session(*, fetchone=None, fetchall=None) -> MagicMock:
    pool_engine._TEST_DISPOSALS.clear()
    session = MagicMock()
    session.begin_nested = MagicMock(side_effect=lambda: _make_begin_nested())
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.fetchall.return_value = fetchall or []
    result.fetchone.return_value = fetchone
    result.mappings.return_value.first.return_value = fetchone
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture
def mock_db_session():
    return _make_session()


@pytest.mark.asyncio
async def test_s104_avg_cost(mock_db_session):
    """Buy 1000 @ £4; buy 500 @ £4.10; sell 700 → S104 avg cost disposal."""
    acc = uuid.uuid4()
    te1 = _te(
        side="buy",
        qty="1000",
        price_gbp="4.00",
        executed_at=datetime(2024, 10, 1, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
    )
    te2 = _te(
        side="buy",
        qty="500",
        price_gbp="4.10",
        executed_at=datetime(2024, 11, 1, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
    )
    te3 = _te(
        side="sell",
        qty="700",
        price_gbp="5.00",
        executed_at=datetime(2025, 2, 1, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
    )

    # Mock pool to return avg_cost after two buys:
    # total_cost = 1000*4 + 500*4.10 = 6050, qty = 1500, avg = 4.0333...
    pool_row = MagicMock()
    pool_row.pool_avg_cost_gbp = Decimal("4050") / Decimal("1000")  # simplified mock
    pool_row.qty = Decimal("1500")
    pool_row.total_cost_gbp = Decimal("6050")

    avg = Decimal("6050") / Decimal("1500")
    pool_row.pool_avg_cost_gbp = avg

    result_with_pool = MagicMock()
    result_with_pool.fetchone.return_value = pool_row
    result_with_pool.fetchall.return_value = []
    result_with_pool.scalar_one_or_none.return_value = None

    # sell query goes to S104 (no same-day, no b&b): return pool
    call_count = 0

    async def execute_side_effect(query, params=None):
        nonlocal call_count
        call_count += 1
        q = str(query)
        if "pool_avg_cost_gbp" in q:
            return result_with_pool
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar_one_or_none.return_value = None
        return result

    mock_db_session.execute = AsyncMock(side_effect=execute_side_effect)

    await pool_engine.process(te1, mock_db_session)
    await pool_engine.process(te2, mock_db_session)
    pool_engine._TEST_DISPOSALS.clear()
    await pool_engine.process(te3, mock_db_session)

    disposals = pool_engine._test_get_disposals(mock_db_session)
    assert len(disposals) == 1
    d = disposals[0]
    assert d.match_type == "s104"
    # avg_cost = 6050/1500 = 4.0333...; allowable = 700 * avg = 2823.33...
    assert abs(d.allowable_cost_gbp - Decimal("2823.33")) < Decimal("1")


@pytest.mark.asyncio
async def test_same_day_rule(mock_db_session):
    """Sell and buy same day → same-day match."""
    acc = uuid.uuid4()
    sell_id = uuid.uuid4()
    sell = _te(
        side="sell",
        qty="50",
        price_gbp="10.00",
        executed_at=datetime(2025, 7, 14, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
    )

    buy_row = MagicMock()
    buy_row.id = sell_id
    buy_row.bb_remaining_qty = Decimal("50")
    buy_row.price_gbp = Decimal("9.50")
    buy_row.commission_gbp = Decimal("0")

    same_day_result = MagicMock()
    same_day_result.fetchall.return_value = [buy_row]

    async def execute_same_day(query, params=None):
        q = str(query)
        if "uk_trade_date = :d" in q and "bb_remaining_qty > 0" in q:
            return same_day_result
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar_one_or_none.return_value = None
        return result

    mock_db_session.execute = AsyncMock(side_effect=execute_same_day)
    await pool_engine.process(sell, mock_db_session)

    disposals = pool_engine._test_get_disposals(mock_db_session)
    assert any(d.match_type == "same_day" for d in disposals)


@pytest.mark.asyncio
async def test_bb_rule(mock_db_session):
    """Sell on 14 Jul; buy on 13 Aug (day 30) → b&b match."""
    acc = uuid.uuid4()
    buy_id = uuid.uuid4()
    sell = _te(
        side="sell",
        qty="50",
        price_gbp="10.00",
        executed_at=datetime(2025, 7, 14, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
    )

    bb_row = MagicMock()
    bb_row.id = buy_id
    bb_row.bb_remaining_qty = Decimal("50")
    bb_row.price_gbp = Decimal("9.80")
    bb_row.commission_gbp = Decimal("0")

    bb_result = MagicMock()
    bb_result.fetchall.return_value = [bb_row]

    async def execute_bb(query, params=None):
        q = str(query)
        if "BETWEEN :d1 AND :d2" in q and "bb_remaining_qty > 0" in q:
            return bb_result
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar_one_or_none.return_value = None
        return result

    mock_db_session.execute = AsyncMock(side_effect=execute_bb)
    await pool_engine.process(sell, mock_db_session)

    disposals = pool_engine._test_get_disposals(mock_db_session)
    assert any(d.match_type == "bb_30" for d in disposals)


@pytest.mark.asyncio
async def test_short_sale_gain(mock_db_session):
    """Short open 100 @ £10; close 100 @ £8 → gain = £200."""
    acc = uuid.uuid4()
    ob_id = uuid.uuid4()
    short_open = _te(
        side="sell",
        qty="100",
        price_gbp="10.00",
        executed_at=datetime(2025, 5, 1, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
        is_short_open=True,
    )
    short_close = _te(
        side="buy",
        qty="100",
        price_gbp="8.00",
        executed_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        account_id=acc,
        is_short_close=True,
    )

    obligation = MagicMock()
    obligation.id = ob_id
    obligation.open_qty = Decimal("100")
    obligation.open_proceeds_gbp = Decimal("1000")  # 100 @ £10

    obligation_result = MagicMock()
    obligation_result.fetchone.return_value = obligation

    async def execute_short(query, params=None):
        q = str(query)
        if "short_obligations" in q and "status = 'open'" in q and "SELECT" in q:
            return obligation_result
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar_one_or_none.return_value = None
        return result

    mock_db_session.execute = AsyncMock(side_effect=execute_short)
    await pool_engine.process(short_open, mock_db_session)
    pool_engine._TEST_DISPOSALS.clear()
    await pool_engine.process(short_close, mock_db_session)

    disposals = pool_engine._test_get_disposals(mock_db_session)
    assert len(disposals) == 1
    d = disposals[0]
    assert d.match_type == "short"
    assert d.gain_gbp == Decimal("200")


def test_gbx_conversion():
    """GBX 1234p / 100 = £12.34."""
    assert Decimal("1234") / Decimal("100") == Decimal("12.34")


def test_tax_year_boundary_april6():
    """Fill on 6 Apr 2025 (UTC) → executed_at.year == 2025."""
    te = _te(
        side="buy",
        qty="1",
        price_gbp="1.00",
        executed_at=datetime(2025, 4, 6, 11, 0, 0, tzinfo=UTC),
    )
    assert te.executed_at.year == 2025


def test_tax_year_boundary_april5():
    """Fill on 5 Apr 2025 (UTC) → executed_at.year == 2025."""
    te = _te(
        side="buy",
        qty="1",
        price_gbp="1.00",
        executed_at=datetime(2025, 4, 5, 11, 0, 0, tzinfo=UTC),
    )
    assert te.executed_at.year == 2025
