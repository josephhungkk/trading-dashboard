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
    # 0011a originally seeded the 16 supported combos below; later migrations
    # (TRAIL/MOC/MOO/LOC/LOO + extra TIFs + asset_class PK widening) add more
    # supported rows. Assert ⊇ on the 0011a baseline so this stays a
    # post-0011a-state check that survives later flips.
    baseline_supported = {
        (o, t)
        for o in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
        for t in ("DAY", "GTC", "IOC", "FOK")
    }
    actual = {(r.order_type, r.time_in_force) for r in rows}
    assert actual >= baseline_supported, f"missing baseline rows: {baseline_supported - actual}"


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
    # 0011a left 34 unsupported rows; subsequent migrations widened the PK
    # with asset_class (0018) so the row count grows. Floor-check only.
    assert n >= 34, f"expected >= 34 unsupported rows, got {n}"
