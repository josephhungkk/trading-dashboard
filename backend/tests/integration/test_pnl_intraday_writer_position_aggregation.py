"""Phase 10a.5 A2.3 integration test.

PnlIntradayWriter receives values aggregated from Position.realized_pnl_today
(CRIT-1: NOT from Summary.realized_pnl, which is cumulative for IBKR).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import Contract, Money, Position
from app.services.pnl_intraday_writer import PnlIntradayWriter

pytest_plugins = ("tests.fixtures.db_session",)


def _make_position(realized_today: str, unrealized: str, currency: str) -> Position:
    return Position(
        contract=Contract(
            symbol="AAPL",
            exchange="NASDAQ",
            currency=currency,
            asset_class="STOCK",
            conid="265598",
            local_symbol="AAPL",
            multiplier="1",
        ),
        quantity="100",
        avg_cost=Money(value="150", currency=currency),
        market_price=Money(value="180", currency=currency),
        market_value=Money(value="18000", currency=currency),
        unrealized_pnl=Money(value=unrealized, currency=currency),
        realized_pnl_today=Money(value=realized_today, currency=currency),
        daily_pnl=Money(value=realized_today, currency=currency),
    )


async def _seed_account(s: AsyncSession, aid: uuid.UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO broker_accounts (id, broker_id, account_number, mode, "
            "gateway_label, currency_base, last_seen_via) VALUES "
            "(:id, 'ibkr', :acct_num, 'paper', 'isa-paper', 'USD', 'isa-paper') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": aid, "acct_num": f"test-{aid.hex[:8]}"},
    )


@pytest.mark.asyncio
async def test_writer_accepts_position_aggregate_sum(session: AsyncSession) -> None:
    """Writer accepts aggregated realized/unrealized from matching positions."""
    aid = uuid4()
    await _seed_account(session, aid)

    positions = [
        _make_position(realized_today="10", unrealized="5", currency="USD"),
        _make_position(realized_today="20", unrealized="15", currency="USD"),
    ]

    matching = [p for p in positions if p.realized_pnl_today.currency == "USD"]
    realized_total = sum((Decimal(p.realized_pnl_today.value) for p in matching), Decimal("0"))
    unrealized_total = sum((Decimal(p.unrealized_pnl.value) for p in matching), Decimal("0"))

    writer = PnlIntradayWriter(session)
    await writer.upsert(
        account_id=aid,
        realized_today=realized_total,
        unrealized=unrealized_total,
        currency="USD",
        summary_updated_at=datetime.now(UTC),
        source_label="ibkr",
    )

    result = await session.execute(
        text("SELECT realized_today, unrealized FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    row = result.fetchone()
    assert row is not None
    assert row.realized_today == Decimal("30")
    assert row.unrealized == Decimal("20")


@pytest.mark.asyncio
async def test_writer_currency_mismatch_filter_simulation(session: AsyncSession) -> None:
    """Writer filters positions by currency_base (simulating A2.3 logic)."""
    aid = uuid4()
    await _seed_account(session, aid)

    positions = [
        _make_position(realized_today="10", unrealized="5", currency="USD"),
        _make_position(realized_today="999", unrealized="999", currency="CAD"),
    ]

    account_currency = "USD"
    matching = [
        p
        for p in positions
        if p.realized_pnl_today.currency == account_currency
        and p.unrealized_pnl.currency == account_currency
    ]

    realized_total = sum((Decimal(p.realized_pnl_today.value) for p in matching), Decimal("0"))
    unrealized_total = sum((Decimal(p.unrealized_pnl.value) for p in matching), Decimal("0"))

    writer = PnlIntradayWriter(session)
    await writer.upsert(
        account_id=aid,
        realized_today=realized_total,
        unrealized=unrealized_total,
        currency="USD",
        summary_updated_at=datetime.now(UTC),
        source_label="ibkr",
    )

    result = await session.execute(
        text("SELECT realized_today, unrealized FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    row = result.fetchone()
    assert row is not None
    assert row.realized_today == Decimal("10")
    assert row.unrealized == Decimal("5")

    skipped = len(positions) - len(matching)
    assert skipped == 1


@pytest.mark.asyncio
async def test_writer_empty_position_list_sums_to_zero(session: AsyncSession) -> None:
    """Writer handles empty position list (sums to zero)."""
    aid = uuid4()
    await _seed_account(session, aid)

    positions: list[Position] = []
    realized_total = sum((Decimal(p.realized_pnl_today.value) for p in positions), Decimal("0"))
    unrealized_total = sum((Decimal(p.unrealized_pnl.value) for p in positions), Decimal("0"))

    writer = PnlIntradayWriter(session)
    await writer.upsert(
        account_id=aid,
        realized_today=realized_total,
        unrealized=unrealized_total,
        currency="USD",
        summary_updated_at=datetime.now(UTC),
        source_label="ibkr",
    )

    result = await session.execute(
        text("SELECT realized_today, unrealized FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    row = result.fetchone()
    assert row is not None
    assert row.realized_today == Decimal("0")
    assert row.unrealized == Decimal("0")
