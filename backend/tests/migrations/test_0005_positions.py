"""Migration 0005 — positions table constraint tests.

Mirrors test_0004 patterns: outer-rollback `session` fixture from
tests.fixtures.db_session + session.begin_nested() savepoints for
IntegrityError tolerance.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_ACCT_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_ACCT_BASE_VALS = "'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper'"


async def _seed_account(session: AsyncSession, account_number: str) -> str:
    await session.execute(
        text(f"INSERT INTO broker_accounts ({_ACCT_BASE_COLS}) VALUES ({_ACCT_BASE_VALS})"),
        {"acct_num": account_number},
    )
    row = (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :acct_num"),
            {"acct_num": account_number},
        )
    ).one()
    return str(row[0])


async def _insert_position(
    session: AsyncSession,
    *,
    account_id: str,
    conid: str,
    qty: str = "100",
    avg_cost: str = "150.50",
    currency: str = "USD",
    multiplier: str = "1",
    asset_class: str = "STOCK",
) -> None:
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:account_id, :conid, :qty, :avg_cost, :currency, :multiplier, :asset_class)"
        ),
        {
            "account_id": account_id,
            "conid": conid,
            "qty": qty,
            "avg_cost": avg_cost,
            "currency": currency,
            "multiplier": multiplier,
            "asset_class": asset_class,
        },
    )


@pytest.mark.asyncio
async def test_positions_composite_primary_key(session: AsyncSession) -> None:
    """Same (account_id, conid) cannot be inserted twice."""
    acct_id = await _seed_account(session, "TEST_PK_001")
    await _insert_position(session, account_id=acct_id, conid="265598")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598")


@pytest.mark.asyncio
async def test_positions_currency_check_rejects_lowercase(session: AsyncSession) -> None:
    """CHECK constraint matches `^[A-Z]{3}$` — lowercase rejected."""
    acct_id = await _seed_account(session, "TEST_CURR_001")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598", currency="usd")


@pytest.mark.asyncio
async def test_positions_currency_check_rejects_4chars(session: AsyncSession) -> None:
    """CHECK constraint length=3 — 4-char currency rejected."""
    acct_id = await _seed_account(session, "TEST_CURR_002")
    with pytest.raises((IntegrityError, DataError, DBAPIError)):
        async with session.begin_nested():
            await _insert_position(session, account_id=acct_id, conid="265598", currency="USDD")


@pytest.mark.asyncio
async def test_positions_qty_overflow_rejected(session: AsyncSession) -> None:
    """NUMERIC(20,8) overflow at qty > 999_999_999_999.99999999 (12 integer digits max)."""
    acct_id = await _seed_account(session, "TEST_OFLOW_001")
    with pytest.raises((DataError, DBAPIError)):
        async with session.begin_nested():
            await _insert_position(
                session,
                account_id=acct_id,
                conid="265598",
                qty="9999999999999.99",
            )


@pytest.mark.asyncio
async def test_positions_cascade_on_account_hard_delete(session: AsyncSession) -> None:
    """ON DELETE CASCADE removes positions when broker_accounts row hard-deleted."""
    acct_id = await _seed_account(session, "TEST_CASCADE_001")
    await _insert_position(session, account_id=acct_id, conid="265598")
    await _insert_position(session, account_id=acct_id, conid="272093")

    await session.execute(
        text("DELETE FROM broker_accounts WHERE id::text = :acct_id"),
        {"acct_id": acct_id},
    )
    remaining = await session.execute(
        text("SELECT COUNT(*) FROM positions WHERE account_id::text = :acct_id"),
        {"acct_id": acct_id},
    )
    assert remaining.scalar_one() == 0
