"""Phase 8b T-S.3 -- verify Schwab capability flip after 0013 runs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Rows flipped by this migration (0013_schwab_capability_flip).
_PHASE_8B_ROWS = frozenset(
    [
        # Trail family
        ("TRAIL", "DAY"),
        ("TRAIL", "GTC"),
        ("TRAIL_LIMIT", "DAY"),
        ("TRAIL_LIMIT", "GTC"),
        # Auction-session orders
        ("MOC", "DAY"),
        ("MOO", "DAY"),
        ("LOC", "DAY"),
        ("LOO", "DAY"),
        # GTD combos
        ("LIMIT", "GTD"),
        ("STOP", "GTD"),
        ("STOP_LIMIT", "GTD"),
        ("TRAIL", "GTD"),
        ("TRAIL_LIMIT", "GTD"),
    ]
)


@pytest.mark.asyncio
async def test_schwab_phase8b_rows_supported(db_session: AsyncSession) -> None:
    """Total supported Schwab rows must reach at least 25 (16 from 0011a + 13 new)."""
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM broker_order_capability "
            "WHERE broker_id = 'schwab' AND is_supported = TRUE"
        )
    )
    n = result.scalar_one()
    # 16 rows from 0011a + 13 rows from 0013 = 29; ON CONFLICT means overlaps
    # resolve idempotently, so the floor is 25 to allow for any pre-existing data.
    assert n >= 25, f"expected >= 25 supported Schwab rows, got {n}"


@pytest.mark.asyncio
async def test_schwab_phase8b_each_row_supported(db_session: AsyncSession) -> None:
    """Every specific (order_type, time_in_force) pair added by 0013 must be supported."""
    rows = (
        await db_session.execute(
            text(
                "SELECT order_type, time_in_force FROM broker_order_capability "
                "WHERE broker_id = 'schwab' AND is_supported = TRUE"
            )
        )
    ).all()
    actual = {(r.order_type, r.time_in_force) for r in rows}
    missing = _PHASE_8B_ROWS - actual
    assert not missing, f"phase 8b rows not flipped to supported: {missing}"


@pytest.mark.asyncio
async def test_schwab_phase8a_rows_still_supported(db_session: AsyncSession) -> None:
    """The 16 rows flipped by 0011a must remain supported after 0013 runs."""
    phase_8a_rows = frozenset(
        (o, t)
        for o in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
        for t in ("DAY", "GTC", "IOC", "FOK")
    )
    rows = (
        await db_session.execute(
            text(
                "SELECT order_type, time_in_force FROM broker_order_capability "
                "WHERE broker_id = 'schwab' AND is_supported = TRUE"
            )
        )
    ).all()
    actual = {(r.order_type, r.time_in_force) for r in rows}
    missing = phase_8a_rows - actual
    assert not missing, f"phase 8a rows were dropped: {missing}"
