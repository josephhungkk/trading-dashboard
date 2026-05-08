"""Phase 8b T-I.5 -- verify IBKR capability flip after 0015 runs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_ibkr_phase8b_capability_count(db_session: AsyncSession) -> None:
    """Total supported IBKR rows must reach at least 20 after 0015."""
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM broker_order_capability "
            "WHERE broker_id = 'ibkr' AND is_supported = TRUE"
        )
    )
    n = result.scalar_one()
    # 4 from 0011 baseline + 21 net new from 0015; floor of 20 absorbs overlaps.
    assert n >= 20, f"expected >= 20 supported IBKR rows, got {n}"


@pytest.mark.asyncio
async def test_ibkr_trail_day_supported(db_session: AsyncSession) -> None:
    """TRAIL + DAY must be supported for IBKR after 0015."""
    is_supported = (
        await db_session.execute(
            text(
                "SELECT is_supported FROM broker_order_capability "
                "WHERE broker_id = 'ibkr' AND order_type = 'TRAIL' "
                "AND time_in_force = 'DAY' AND asset_class = 'STOCK'"
            )
        )
    ).scalar_one()
    assert is_supported is True
