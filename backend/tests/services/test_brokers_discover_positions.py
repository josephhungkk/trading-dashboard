"""Phase 5b.1 A4: BrokerDiscoverer._discover_positions fan-out tests.

Mirrors the NLV fan-out test pattern. The discoverer opens its own session
via ``self._session_factory()`` in production. To share visibility with the
outer-rollback ``session`` fixture, we inject a factory that yields the same
session and remap ``session.begin`` -> ``session.begin_nested`` so the
``async with self._session_factory() as s, s.begin():`` line in production
becomes a savepoint inside the test's outer transaction.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers import base
from app.services.brokers import BrokerDiscoverer

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).scalar_one()


def _mk_position(
    conid: str,
    qty: str = "100",
    avg_cost: str = "150",
    currency: str = "USD",
    multiplier: str = "1",
    asset_class: str = "STOCK",
) -> base.Position:
    return base.Position(
        contract=base.Contract(
            conid=conid,
            symbol=conid,
            exchange="SMART",
            currency=currency,
            asset_class=asset_class,
            multiplier=multiplier,
            local_symbol="",
        ),
        quantity=qty,
        avg_cost=base.Money(value=avg_cost, currency=currency),
        market_price=base.Money(value="0", currency=currency),
        market_value=base.Money(value="0", currency=currency),
        unrealized_pnl=base.Money(value="0", currency=currency),
        realized_pnl_today=base.Money(value="0", currency=currency),
        daily_pnl=base.Money(value="0", currency=currency),
    )


def _make_factory(session: AsyncSession):
    """Yield the shared test session and remap .begin -> .begin_nested.

    Production code: ``async with self._session_factory() as s, s.begin():``.
    We need both context managers to be no-ops/savepoints against the test's
    outer transaction so seeded data stays visible inside _discover_positions.
    """

    @asynccontextmanager
    async def _cm():
        original_begin = session.begin
        session.begin = session.begin_nested  # type: ignore[method-assign]
        try:
            yield session
        finally:
            session.begin = original_begin  # type: ignore[method-assign]

    return _cm


def _make_registry(client: MagicMock) -> MagicMock:
    registry = MagicMock()
    registry.get_client = AsyncMock(return_value=client)
    return registry


@pytest.mark.asyncio
async def test_fan_out_upserts_positions(session: AsyncSession) -> None:
    """Happy path: GetPositions returns 2 positions, both upserted."""
    acct_id = await _seed_account(session, "TEST_FAN_001")

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598"), _mk_position("272093")])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_FAN_001")])

    rows = await session.execute(
        text("SELECT conid FROM positions WHERE account_id::text = :a ORDER BY conid"),
        {"a": acct_id},
    )
    assert [r[0] for r in rows.all()] == ["265598", "272093"]


@pytest.mark.asyncio
async def test_rpc_failure_leaves_existing_rows(session: AsyncSession) -> None:
    """RPC raises -> existing positions for that account untouched."""
    acct_id = await _seed_account(session, "TEST_RPC_FAIL_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"
        ),
        {"a": acct_id},
    )

    client = MagicMock()
    client.get_positions = AsyncMock(side_effect=TimeoutError())
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_RPC_FAIL_001")])

    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
            {"a": acct_id},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_empty_response_deletes_all_positions(session: AsyncSession) -> None:
    """Successful empty response (account liquidated) -> all rows deleted."""
    acct_id = await _seed_account(session, "TEST_EMPTY_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"
        ),
        {"a": acct_id},
    )

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_EMPTY_001")])

    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
            {"a": acct_id},
        )
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_overflow_savepoint_isolates(session: AsyncSession) -> None:
    """NUMERIC(20,8) overflow on one account doesn't break others."""
    acct_a = await _seed_account(session, "TEST_OF_A")
    acct_b = await _seed_account(session, "TEST_OF_B")

    bad_pos = _mk_position("265598", qty="9" * 25)  # overflow
    good_pos = _mk_position("272093")

    async def get_positions(account_number: str) -> list[base.Position]:
        return [bad_pos] if account_number == "TEST_OF_A" else [good_pos]

    client = MagicMock()
    client.get_positions = AsyncMock(side_effect=get_positions)
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_OF_A"), ("isa-paper", "TEST_OF_B")])

    rows_a = (
        await session.execute(
            text("SELECT COUNT(*) FROM positions WHERE account_id::text = :a"),
            {"a": acct_a},
        )
    ).scalar_one()
    rows_b = (
        await session.execute(
            text("SELECT COUNT(*) FROM positions WHERE account_id::text = :b"),
            {"b": acct_b},
        )
    ).scalar_one()
    assert rows_a == 0
    assert rows_b == 1


@pytest.mark.asyncio
async def test_delta_delete_removes_vanished_position(session: AsyncSession) -> None:
    """Position present last tick, absent this tick -> deleted."""
    acct_id = await _seed_account(session, "TEST_VANISH_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK'), "
            "       (:a, '272093', '50', '300', 'USD', '1', 'STOCK')"
        ),
        {"a": acct_id},
    )

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598")])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_VANISH_001")])

    rows = (
        await session.execute(
            text("SELECT conid FROM positions WHERE account_id::text = :a ORDER BY conid"),
            {"a": acct_id},
        )
    ).all()
    assert [r[0] for r in rows] == ["265598"]


@pytest.mark.asyncio
async def test_qty_update_advances_updated_at(session: AsyncSession) -> None:
    """Same conid, new qty -> updated_at progresses on UPDATE branch."""
    acct_id = await _seed_account(session, "TEST_UPD_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class, updated_at) "
            "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK', "
            "        '2026-01-01'::timestamptz)"
        ),
        {"a": acct_id},
    )

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598", qty="200")])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_UPD_001")])

    row = (
        await session.execute(
            text(
                "SELECT qty, updated_at > '2026-01-02'::timestamptz "
                "FROM positions WHERE account_id::text = :a"
            ),
            {"a": acct_id},
        )
    ).one()
    assert Decimal(row[0]) == Decimal("200")
    assert row[1] is True


@pytest.mark.asyncio
async def test_currency_flip_persists(session: AsyncSession) -> None:
    """Currency change mid-life (USD -> GBP) is honoured by upsert."""
    acct_id = await _seed_account(session, "TEST_CURR_FLIP_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:a, '265598', '100', '150', 'USD', '1', 'STOCK')"
        ),
        {"a": acct_id},
    )

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[_mk_position("265598", currency="GBP")])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_CURR_FLIP_001")])

    row = (
        await session.execute(
            text("SELECT currency FROM positions WHERE account_id::text = :a"),
            {"a": acct_id},
        )
    ).one()
    assert row[0] == "GBP"


@pytest.mark.asyncio
async def test_metric_emitted(session: AsyncSession) -> None:
    """Histogram observes a duration sample on each call."""
    from app.core import metrics

    before = metrics.broker_discover_positions_update_duration_ms._sum.get()

    await _seed_account(session, "TEST_METRIC_001")

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    discoverer = BrokerDiscoverer(_make_registry(client), _make_factory(session))

    await discoverer._discover_positions([("isa-paper", "TEST_METRIC_001")])

    after = metrics.broker_discover_positions_update_duration_ms._sum.get()
    assert after > before
