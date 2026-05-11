"""
Phase 10a.5 A2: PnlIntradayWriter upsert + prune tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.pnl_intraday_writer import PnlIntradayWriter

pytest_plugins = ("tests.fixtures.db_session",)


async def _seed_account(s: AsyncSession, aid: uuid.UUID) -> None:
    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, account_number, mode, gateway_label, "
            " currency_base, last_seen_via) "
            "VALUES (:id, 'ibkr', :acct_num, 'paper', 'isa-paper', "
            " 'USD', 'isa-paper') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": aid, "acct_num": f"test-{aid.hex[:8]}"},
    )


@pytest.mark.asyncio
async def test_upsert_inserts_row(session: AsyncSession) -> None:
    aid = uuid4()
    await _seed_account(session, aid)

    writer = PnlIntradayWriter(session)
    now = datetime.now(UTC)
    await writer.upsert(
        account_id=aid,
        realized_today=Decimal("123.45"),
        unrealized=Decimal("-50.00"),
        currency="USD",
        summary_updated_at=now,
        source_label="ibkr",
    )

    result = await session.execute(
        text("SELECT realized_today, unrealized FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    row = result.one()
    assert row.realized_today == Decimal("123.45")
    assert row.unrealized == Decimal("-50.00")


@pytest.mark.asyncio
async def test_upsert_stale_summary_rejected(session: AsyncSession) -> None:
    aid = uuid4()
    await _seed_account(session, aid)

    writer = PnlIntradayWriter(session)
    fresh_ts = datetime.now(UTC)
    stale_ts = fresh_ts - timedelta(seconds=120)

    await writer.upsert(
        account_id=aid,
        realized_today=Decimal("100"),
        unrealized=Decimal("0"),
        currency="USD",
        summary_updated_at=fresh_ts,
        source_label="ibkr",
    )

    await writer.upsert(
        account_id=aid,
        realized_today=Decimal("999"),
        unrealized=Decimal("999"),
        currency="USD",
        summary_updated_at=stale_ts,
        source_label="ibkr",
    )

    result = await session.execute(
        text("SELECT realized_today FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    row = result.scalar_one()
    assert row == Decimal("100")


@pytest.mark.asyncio
async def test_upsert_unchanged_is_noop(session: AsyncSession) -> None:
    aid = uuid4()
    await _seed_account(session, aid)

    writer = PnlIntradayWriter(session)
    ts1 = datetime.now(UTC)
    ts2 = ts1 + timedelta(seconds=30)

    await writer.upsert(
        account_id=aid,
        realized_today=Decimal("100"),
        unrealized=Decimal("0"),
        currency="USD",
        summary_updated_at=ts1,
        source_label="ibkr",
    )

    result1 = await session.execute(
        text("SELECT updated_at FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    first_updated = result1.scalar_one()

    await writer.upsert(
        account_id=aid,
        realized_today=Decimal("100"),
        unrealized=Decimal("0"),
        currency="USD",
        summary_updated_at=ts2,
        source_label="ibkr",
    )

    result2 = await session.execute(
        text("SELECT updated_at FROM pnl_intraday WHERE account_id = :aid"),
        {"aid": aid},
    )
    second_updated = result2.scalar_one()

    assert first_updated == second_updated


@pytest.mark.asyncio
async def test_prune_drops_old_rows(session: AsyncSession) -> None:
    aid = uuid4()
    await _seed_account(session, aid)

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    old_day = now - timedelta(days=45)
    young_day = now - timedelta(days=5)

    insert_sql = (
        "INSERT INTO pnl_intraday "
        "(account_id, day_start_utc, realized_today, unrealized, "
        " currency, summary_updated_at, source_label) "
        "VALUES (:aid, :day, 0, 0, 'USD', :day, 'ibkr')"
    )
    await session.execute(text(insert_sql), {"aid": aid, "day": old_day})
    await session.execute(text(insert_sql), {"aid": aid, "day": young_day})

    writer = PnlIntradayWriter(session)
    deleted = await writer.prune_older_than(days=30)

    assert deleted == 1
