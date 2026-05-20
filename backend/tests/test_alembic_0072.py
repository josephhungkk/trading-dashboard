"""Migration 0072 smoke test: bot_health_snapshots table created."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_bot_health_snapshots_table_exists(session: AsyncSession) -> None:
    result = await session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = 'bot_health_snapshots'")
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_bot_health_snapshots_columns(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'bot_health_snapshots'"
            " ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    expected = {
        "bot_id",
        "snapshot_at",
        "sharpe_30d",
        "sharpe_7d",
        "max_drawdown",
        "win_rate",
        "total_pnl",
        "trade_count",
        "advisor_veto_accuracy_1h",
        "exposure_utilisation",
    }
    assert expected.issubset(cols)
