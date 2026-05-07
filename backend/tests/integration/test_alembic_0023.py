"""Phase 9 Task 1 — verify timescaledb extension is present after migration 0023."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_0023_timescaledb_extension_present(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text("SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb'")
        )
    ).first()
    assert row is not None, "timescaledb extension missing after 0023"
    major, minor = (int(p) for p in row.extversion.split(".")[:2])
    assert (major, minor) >= (2, 17), f"Timescale {row.extversion} < required 2.17"
