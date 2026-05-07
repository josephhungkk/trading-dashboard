"""Phase 9 Task 7 — verify app_config charts-namespace seed is correct + idempotent."""

from __future__ import annotations

import pytest
from backend.scripts.seed_phase9_app_config import seed_phase9_app_config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration]


EXPECTED_KEYS: dict[str, str] = {
    "bar_source_priority.equity_us": "json",
    "bar_source_priority.equity_hk": "json",
    "bar_source_priority.crypto": "json",
    "bar_source_priority.fx": "json",
    "bar_pre_warm_window_days": "int",
    "bar_active_set_recency_days": "int",
    "chart_layout_schema_version": "int",
    "enabled": "bool",
}


@pytest.mark.asyncio
async def test_seed_writes_eight_keys(db_session: AsyncSession) -> None:
    await seed_phase9_app_config(db_session)
    rows = (
        await db_session.execute(
            text("SELECT key, value_type FROM app_config WHERE namespace='charts'")
        )
    ).all()
    by_key = {r.key: r.value_type for r in rows}
    assert by_key == EXPECTED_KEYS, f"app_config charts namespace mismatch: {by_key}"


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await seed_phase9_app_config(db_session)
    await seed_phase9_app_config(db_session)
    cnt = (
        await db_session.execute(
            text("SELECT COUNT(*)::int AS c FROM app_config WHERE namespace='charts'")
        )
    ).scalar_one()
    assert cnt == 8
