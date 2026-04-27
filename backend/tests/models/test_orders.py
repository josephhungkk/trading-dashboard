from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.models.orders import Order, OrderEvent

pytest_plugins = ("tests.migrations.conftest",)

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> uuid.UUID:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    row = (
        await session.execute(
            text("SELECT id FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).first()
    assert row is not None
    return row[0]


def _minimal_order(account_id: uuid.UUID, **overrides: object) -> Order:
    values: dict[str, object] = {
        "id": uuid7(),
        "account_id": account_id,
        "client_order_id": uuid.uuid4(),
        "conid": "265598",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "tif": "DAY",
        "qty": Decimal("1"),
        "status": "pending_submit",
        "notional": Decimal("1000"),
    }
    values.update(overrides)
    return Order(**values)


def _minimal_event(account_id: uuid.UUID, **overrides: object) -> OrderEvent:
    values: dict[str, object] = {
        "order_id": None,
        "account_id": account_id,
        "status": "submitted",
        "broker_event_at": datetime.now(UTC),
    }
    values.update(overrides)
    return OrderEvent(**values)


@pytest.mark.asyncio
async def test_order_can_insert_with_uuidv7_id(session: AsyncSession) -> None:
    account_id = await _seed_account(session, "TEST_MODEL_ORDER_UUIDV7")
    order = _minimal_order(account_id)

    session.add(order)
    await session.flush()

    row = await session.get(Order, order.id)
    assert row is not None
    assert isinstance(row.id, uuid.UUID)


@pytest.mark.asyncio
async def test_order_status_enum_values(session: AsyncSession) -> None:
    account_id = await _seed_account(session, "TEST_MODEL_ORDER_STATUSES")
    statuses = [
        "pending_submit",
        "submitted",
        "partial",
        "filled",
        "cancelled",
        "rejected",
        "expired",
        "inactive",
    ]

    for status in statuses:
        order = _minimal_order(account_id, status=status)
        session.add(order)
        await session.flush()
        assert order.status == status

    # asyncpg raises DBAPIError (wrapping InvalidTextRepresentationError) for
    # invalid enum values; broader IntegrityError/DataError don't match.
    with pytest.raises((IntegrityError, DataError, DBAPIError)):
        async with session.begin_nested():
            bad_order = _minimal_order(account_id, status="unknown")
            session.add(bad_order)
            await session.flush()


@pytest.mark.asyncio
async def test_order_event_can_have_null_order_id(session: AsyncSession) -> None:
    account_id = await _seed_account(session, "TEST_MODEL_EVENT_NULL_ORDER")
    event = _minimal_event(account_id, order_id=None)

    session.add(event)
    await session.flush()

    row = await session.get(OrderEvent, event.id)
    assert row is not None
    assert row.order_id is None


@pytest.mark.asyncio
async def test_order_relationship_loads_events(session: AsyncSession) -> None:
    account_id = await _seed_account(session, "TEST_MODEL_ORDER_EVENTS")
    order = _minimal_order(account_id)
    earlier = datetime.now(UTC) - timedelta(minutes=1)
    later = datetime.now(UTC)
    session.add(order)
    await session.flush()
    order_id = order.id
    session.add_all(
        [
            _minimal_event(
                account_id, order_id=order_id, status="submitted", broker_event_at=earlier
            ),
            _minimal_event(account_id, order_id=order_id, status="partial", broker_event_at=later),
        ]
    )
    await session.flush()

    session.expire_all()
    reloaded = await session.get(Order, order_id)

    assert reloaded is not None
    assert len(reloaded.events) == 2
    assert reloaded.events[0].broker_event_at == later


@pytest.mark.asyncio
async def test_order_repr_omits_secrets(session: AsyncSession) -> None:
    account_id = uuid.uuid4()
    order = _minimal_order(account_id, symbol="MSFT", side="SELL", qty=Decimal("2"))

    order_repr = repr(order)

    assert "MSFT" in order_repr
    assert "SELL" in order_repr
    assert "2" in order_repr
    assert "raw_payload" not in order_repr
