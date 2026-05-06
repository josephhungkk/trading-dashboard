"""Phase 8a A5 -- verify Schwab capability flip after 0011a runs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_schwab_supported_combos_after_flip(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT order_type, time_in_force FROM broker_order_capability "
                "WHERE broker_id = 'schwab' AND is_supported = TRUE "
                "ORDER BY order_type, time_in_force"
            )
        )
    ).all()
    expected = {
        (o, t)
        for o in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
        for t in ("DAY", "GTC", "IOC", "FOK")
    }
    actual = {(r.order_type, r.time_in_force) for r in rows}
    assert actual == expected, f"unexpected supported set: {actual ^ expected}"
    assert len(rows) == 16


@pytest.mark.asyncio
async def test_schwab_unsupported_rows_unchanged(db_session: AsyncSession) -> None:
    n = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM broker_order_capability "
                "WHERE broker_id = 'schwab' AND is_supported = FALSE"
            )
        )
    ).scalar_one()
    # Schwab has 10 order_types x 5 TIFs = 50 rows total; 16 flipped supported,
    # 34 remain unsupported (TRAIL/TRAIL_LIMIT/MOC/MOO/LOC/LOO across all TIFs +
    # the GTD combos for the supported types).
    assert n == 50 - 16
