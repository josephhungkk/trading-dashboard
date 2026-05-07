"""Phase 8c T-B-eq.3 -- verify Alpaca STOCK BRACKET capability flip."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_alpaca_equity_bracket_supported(db_session: AsyncSession) -> None:
    """Alpaca STOCK BRACKET DAY should be enabled after 0021."""
    n = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'STOCK' "
                "AND order_type = 'BRACKET' "
                "AND time_in_force = 'DAY' "
                "AND is_supported = TRUE"
            )
        )
    ).scalar_one()
    assert n == 1
