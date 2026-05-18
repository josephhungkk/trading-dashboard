from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update

from app.models.combos import ComboOrder, OrderLeg
from app.models.instruments import Instrument
from app.models.orders import Order
from app.services.combos.combo_fill_listener import _recompute_combo_status, handle_fill

pytestmark = pytest.mark.asyncio


async def _account_id(db) -> UUID:
    result = await db.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
    row = result.scalar_one_or_none()
    if row is not None:
        return UUID(str(row))
    r2 = await db.execute(
        text(
            "INSERT INTO broker_accounts"
            " (broker_id, account_number, alias, mode, gateway_label, last_seen_via, currency_base)"
            " VALUES ('ibkr', 'FILL_TEST01', 'fill-test', 'paper', 'ibkr-ci', 'ibkr-ci', 'USD')"
            " RETURNING id"
        )
    )
    await db.commit()
    return UUID(str(r2.scalar_one()))


async def _instrument_id(db) -> int:
    result = await db.execute(select(Instrument.id).limit(1))
    row = result.scalar_one_or_none()
    if row is not None:
        return int(row)
    r2 = await db.execute(
        text(
            "INSERT INTO instruments"
            " (canonical_id, asset_class, primary_exchange, currency, display_name)"
            " VALUES ('test:AAPL_FILL:ci', 'OPTION', 'CBOE', 'USD', 'AAPL Fill Test')"
            " ON CONFLICT (canonical_id) DO NOTHING RETURNING id"
        )
    )
    await db.commit()
    return int(r2.scalar_one())


async def _make_combo(db, status: str = "working") -> ComboOrder:
    combo = ComboOrder(
        account_id=await _account_id(db),
        client_combo_id=f"combo-{uuid4()}",
        strategy_type="VERTICAL",
        underlying_symbol="AAPL",
        underlying_canonical_id="AAPL",
        net_debit_credit=Decimal("3.10"),
        net_debit_credit_kind="DEBIT",
        tif="DAY",
        status=status,
    )
    db.add(combo)
    await db.flush()
    return combo


async def _make_order(db, combo_id, account_id) -> Order:
    order = Order(
        account_id=account_id,
        client_order_id=uuid4(),
        combo_id=combo_id,
        status="submitted",
        conid="combo-leg",
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty=Decimal("1"),
        limit_price=Decimal("5.00"),
        symbol="AAPL",
        notional=Decimal("5.00"),
    )
    db.add(order)
    await db.flush()
    return order


async def _make_leg(
    db,
    combo_id,
    order_id,
    leg_idx: int,
    instrument_id: int,
    status: str = "working",
) -> OrderLeg:
    leg = OrderLeg(
        combo_id=combo_id,
        order_id=order_id,
        leg_idx=leg_idx,
        instrument_id=instrument_id,
        side="buy",
        qty=Decimal("1"),
        position_effect="OPEN",
        status=status,
    )
    db.add(leg)
    await db.flush()
    return leg


async def test_handle_fill_noop_for_non_combo_order(db_session) -> None:
    order = Order(
        account_id=await _account_id(db_session),
        client_order_id=uuid4(),
        combo_id=None,
        status="submitted",
        conid="AAPL",
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty=Decimal("1"),
        limit_price=Decimal("5.30"),
        symbol="AAPL",
        notional=Decimal("5.30"),
    )
    db_session.add(order)
    await db_session.flush()

    await handle_fill(db_session, order.id, Decimal("1"), Decimal("5.30"))


async def test_handle_fill_updates_leg_and_combo_status(db_session) -> None:
    inst_id = await _instrument_id(db_session)
    combo = await _make_combo(db_session)
    order = await _make_order(db_session, combo.id, combo.account_id)
    await _make_leg(db_session, combo.id, order.id, 0, inst_id)

    await handle_fill(db_session, order.id, Decimal("1"), Decimal("5.30"))

    leg = (
        await db_session.execute(select(OrderLeg).where(OrderLeg.order_id == order.id))
    ).scalar_one()
    assert leg.status == "filled"
    assert leg.filled_qty == Decimal("1")
    assert leg.avg_fill_price == Decimal("5.30")
    assert combo.status == "filled"


async def test_recompute_status_all_filled(db_session) -> None:
    inst_id = await _instrument_id(db_session)
    combo = await _make_combo(db_session)
    order1 = await _make_order(db_session, combo.id, combo.account_id)
    order2 = await _make_order(db_session, combo.id, combo.account_id)
    await _make_leg(db_session, combo.id, order1.id, 0, inst_id, status="filled")
    await _make_leg(db_session, combo.id, order2.id, 1, inst_id, status="filled")
    await db_session.execute(
        update(OrderLeg).where(OrderLeg.combo_id == combo.id).values(filled_qty=Decimal("1"))
    )

    status = await _recompute_combo_status(db_session, combo.id)

    assert status == "filled"


async def test_recompute_status_partially_filled(db_session) -> None:
    inst_id = await _instrument_id(db_session)
    combo = await _make_combo(db_session)
    order1 = await _make_order(db_session, combo.id, combo.account_id)
    order2 = await _make_order(db_session, combo.id, combo.account_id)
    leg1 = await _make_leg(db_session, combo.id, order1.id, 0, inst_id, status="filled")
    await _make_leg(db_session, combo.id, order2.id, 1, inst_id, status="working")
    await db_session.execute(
        update(OrderLeg).where(OrderLeg.id == leg1.id).values(filled_qty=Decimal("1"))
    )

    status = await _recompute_combo_status(db_session, combo.id)

    assert status == "partially_filled"


async def test_recompute_status_legged_out(db_session) -> None:
    inst_id = await _instrument_id(db_session)
    combo = await _make_combo(db_session)
    order1 = await _make_order(db_session, combo.id, combo.account_id)
    order2 = await _make_order(db_session, combo.id, combo.account_id)
    leg1 = await _make_leg(db_session, combo.id, order1.id, 0, inst_id, status="filled")
    await _make_leg(db_session, combo.id, order2.id, 1, inst_id, status="cancelled")
    await db_session.execute(
        update(OrderLeg).where(OrderLeg.id == leg1.id).values(filled_qty=Decimal("1"))
    )

    status = await _recompute_combo_status(db_session, combo.id)

    assert status == "legged_out"
