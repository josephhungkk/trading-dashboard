from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_0068_adds_outcome_columns(db_session: AsyncSession) -> None:
    """After 0068 runs, bot_advisor_decisions has attribution outcome columns."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='bot_advisor_decisions'"
            "  AND column_name IN ("
            "    'attribution_status','attribution_windows','attribution_computed_at',"
            "    'outcome_15m_correct','outcome_15m_pnl',"
            "    'outcome_1h_correct','outcome_1h_pnl',"
            "    'outcome_4h_correct','outcome_4h_pnl',"
            "    'outcome_eod_correct','outcome_eod_pnl'"
            "  )"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    assert cols == {
        "attribution_status",
        "attribution_windows",
        "attribution_computed_at",
        "outcome_15m_correct",
        "outcome_15m_pnl",
        "outcome_1h_correct",
        "outcome_1h_pnl",
        "outcome_4h_correct",
        "outcome_4h_pnl",
        "outcome_eod_correct",
        "outcome_eod_pnl",
    }


@pytest.mark.asyncio
async def test_0068_attribution_status_default_pending(db_session: AsyncSession) -> None:
    """attribution_status defaults to 'pending'."""
    result = await db_session.execute(
        text(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name='bot_advisor_decisions' AND column_name='attribution_status'"
        )
    )
    default = result.scalar_one_or_none()
    assert default is not None and "pending" in str(default)


@pytest.mark.asyncio
async def test_0068_adds_advisor_decision_id_to_bot_orders(db_session: AsyncSession) -> None:
    """bot_orders.advisor_decision_id FK column exists."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='bot_orders' AND column_name='advisor_decision_id'"
        )
    )
    assert result.scalar_one_or_none() == "advisor_decision_id"


@pytest.mark.asyncio
async def test_0068_attribution_status_check_constraint(db_session: AsyncSession) -> None:
    """attribution_status CHECK constraint exists on bot_advisor_decisions."""
    result = await db_session.execute(
        text(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid='bot_advisor_decisions'::regclass"
            "   AND contype='c' AND conname LIKE '%attribution_status%'"
        )
    )
    assert result.scalar_one_or_none() is not None
