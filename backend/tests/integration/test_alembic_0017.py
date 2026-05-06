"""Phase 8b T-O.12 -- verify OCO capability flip after 0017 runs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_oco_flag_enabled_for_all_three_brokers(db_session: AsyncSession) -> None:
    """All 3 OCO-supported brokers should have feature='oco' is_supported=TRUE."""
    rows = (
        await db_session.execute(
            text(
                "SELECT broker_id, is_supported FROM broker_features "
                "WHERE feature = 'oco' AND broker_id IN ('schwab', 'ibkr', 'futu')"
            )
        )
    ).all()
    flags = {r.broker_id: r.is_supported for r in rows}
    assert flags.get("schwab") is True
    assert flags.get("ibkr") is True
    assert flags.get("futu") is True


@pytest.mark.asyncio
async def test_oco_other_brokers_not_changed(db_session: AsyncSession) -> None:
    """Any broker_id not in (schwab, ibkr, futu) must NOT be flipped to TRUE."""
    rows = (
        await db_session.execute(
            text(
                "SELECT broker_id, is_supported FROM broker_features "
                "WHERE feature = 'oco' AND broker_id NOT IN ('schwab', 'ibkr', 'futu')"
            )
        )
    ).all()
    for row in rows:
        assert row.is_supported is False, f"unexpected oco flip for {row.broker_id}"
