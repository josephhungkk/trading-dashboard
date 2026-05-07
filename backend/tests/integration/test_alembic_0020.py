"""Phase 8c T-S.8 -- verify Alpaca STOCK capability flip."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_alpaca_equity_rows_supported(db_session: AsyncSession) -> None:
    """Alpaca STOCK capability rows should be enabled after 0020."""
    n = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'STOCK' "
                "AND is_supported = TRUE"
            )
        )
    ).scalar_one()
    # 16 rows: MARKET (DAY/GTC) + LIMIT (DAY/GTC/IOC/FOK) + STOP (DAY/GTC) +
    # STOP_LIMIT (DAY/GTC) + TRAIL (DAY/GTC) + MOC/MOO/LOC/LOO (DAY each).
    assert n == 16
