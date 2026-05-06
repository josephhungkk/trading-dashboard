"""Phase 8b T-F.7 -- verify Futu capability flip after 0014 runs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Rows flipped by this migration (0014_futu_capability_flip).
_PHASE_8B_ROWS = frozenset(
    [
        # Trail family
        ("TRAIL", "DAY"),
        ("TRAIL", "GTC"),
        # IOC / FOK on LIMIT
        ("LIMIT", "IOC"),
        ("LIMIT", "FOK"),
        # Stop family
        ("STOP", "DAY"),
        ("STOP", "GTC"),
        ("STOP_LIMIT", "DAY"),
        ("STOP_LIMIT", "GTC"),
        # GTD on LIMIT
        ("LIMIT", "GTD"),
    ]
)


@pytest.mark.asyncio
async def test_futu_phase8b_capability_count(db_session: AsyncSession) -> None:
    """Total supported Futu rows must reach at least 10 after 0014."""
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM broker_order_capability "
            "WHERE broker_id = 'futu' AND is_supported = TRUE"
        )
    )
    n = result.scalar_one()
    # 4 rows from 0011a (MARKET/LIMIT x DAY/GTC) + 9 rows from 0014;
    # floor of 10 accounts for any pre-existing overlaps.
    assert n >= 10, f"expected >= 10 supported Futu rows, got {n}"


@pytest.mark.asyncio
async def test_futu_phase8b_each_row_supported(db_session: AsyncSession) -> None:
    """Every specific (order_type, time_in_force) pair added by 0014 must be supported."""
    rows = (
        await db_session.execute(
            text(
                "SELECT order_type, time_in_force FROM broker_order_capability "
                "WHERE broker_id = 'futu' AND is_supported = TRUE"
            )
        )
    ).all()
    supported = {(r.order_type, r.time_in_force) for r in rows}
    missing = _PHASE_8B_ROWS - supported
    assert not missing, f"Futu capability rows not flipped: {missing}"


@pytest.mark.asyncio
async def test_futu_modify_bracket_enabled(db_session: AsyncSession) -> None:
    """broker_features rows for modify + bracket must be is_supported=TRUE for futu after 0014."""
    rows = (
        await db_session.execute(
            text(
                "SELECT feature, is_supported FROM broker_features "
                "WHERE broker_id = 'futu' AND feature IN ('modify', 'bracket')"
            )
        )
    ).all()
    flags = {r.feature: r.is_supported for r in rows}
    assert flags.get("modify") is True, f"futu modify not enabled, got {flags.get('modify')}"
    assert flags.get("bracket") is True, f"futu bracket not enabled, got {flags.get('bracket')}"
