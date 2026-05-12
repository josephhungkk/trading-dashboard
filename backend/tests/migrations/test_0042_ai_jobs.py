"""Phase 11a-A1: ai_jobs table migration test.

Verifies the 12-column shape, partial index for orphan-recovery scan,
and that the table is NOT a hypertable (LOW-6 — job volume is small
and queries are by status, not time-range).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ai_jobs_columns_and_indices(session: AsyncSession) -> None:
    result = await session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_jobs'")
    )
    cols = {row[0] for row in result.fetchall()}
    expected = {
        "id",
        "jwt_subject",
        "status",
        "capability",
        "request_jsonb",
        "response_jsonb",
        "error",
        "started_at",
        "warming_started_at",
        "inferring_started_at",
        "completed_at",
        "cancel_requested",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_ai_jobs_has_status_started_at_partial_index(session: AsyncSession) -> None:
    """HIGH-8: partial index covers only live states the orphan-recovery
    sweep scans. Full-table index would be wasteful at low cardinality."""
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'ai_jobs' "
            "  AND indexname = 'idx_ai_jobs_status_started_at'"
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_ai_jobs_is_not_hypertable(session: AsyncSession) -> None:
    """LOW-6: ai_jobs deliberately NOT a hypertable — queries by status,
    not time-range; volume is small."""
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'ai_jobs'"
        )
    )
    assert result.scalar_one() == 0
