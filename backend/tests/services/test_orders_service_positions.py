"""Phase 5b.1 A6: _position_qty reads real values from the positions table."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orders_service import _position_qty


async def _seed_acct(session: AsyncSession, acct_num: str) -> str:
    await session.execute(
        text(
            "INSERT INTO broker_accounts (broker_id, account_number, mode, "
            "gateway_label, currency_base, last_seen_via) "
            "VALUES ('ibkr', :n, 'paper', 'isa-paper', 'USD', 'isa-paper')"
        ),
        {"n": acct_num},
    )
    return (
        await session.execute(
            text("SELECT id::text FROM broker_accounts WHERE account_number = :n"),
            {"n": acct_num},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_position_qty_reads_populated_row(session: AsyncSession) -> None:
    """_position_qty returns the qty when a row exists."""
    acct_id = await _seed_acct(session, "TEST_PQ_001")
    await session.execute(
        text(
            "INSERT INTO positions (account_id, conid, qty, avg_cost, currency, "
            "multiplier, asset_class) "
            "VALUES (:a, '265598', '150.5', '99', 'USD', '1', 'STOCK')"
        ),
        {"a": acct_id},
    )

    qty = await _position_qty(session, acct_id, "265598")
    assert qty == Decimal("150.5")


@pytest.mark.asyncio
async def test_position_qty_returns_zero_for_no_row(session: AsyncSession) -> None:
    """No matching row -> Decimal('0')."""
    acct_id = await _seed_acct(session, "TEST_PQ_002")

    qty = await _position_qty(session, acct_id, "999999")
    assert qty == Decimal("0")
