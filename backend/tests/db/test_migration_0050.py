"""Migration 0050: futures tables DDL."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_futures_roll_rules_table(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'futures_roll_rules' ORDER BY ordinal_position"
        )
    )
    cols = [r[0] for r in result]
    assert "id" in cols
    assert "account_id" in cols
    assert "instrument_id" in cols
    assert "days_before" in cols
    assert "enabled" in cols
    assert "updated_at" in cols


@pytest.mark.asyncio
async def test_futures_settlement_events_table(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'futures_settlement_events' ORDER BY ordinal_position"
        )
    )
    cols = [r[0] for r in result]
    assert "id" in cols
    assert "account_id" in cols
    assert "instrument_id" in cols
    assert "settlement_price" in cols
    assert "cash_delta" in cols
    assert "settlement_type" in cols
    assert "broker_event_id" in cols
    assert "settled_at" in cols


@pytest.mark.asyncio
async def test_futures_settlement_events_dedup_index(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'futures_settlement_events' "
            "AND indexdef LIKE '%broker_event_id%'"
        )
    )
    rows = result.fetchall()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_future_asset_class_enum(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT enumlabel FROM pg_enum "
            "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
            "WHERE pg_type.typname = 'instrument_asset_class'"
        )
    )
    labels = [r[0] for r in result]
    assert "FUTURE" in labels
