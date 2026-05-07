"""Phase 8c T-O.5 -- verify Alpaca OCO capability rows."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_equity_oco_supported(db_session: AsyncSession) -> None:
    n = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'STOCK' "
                "AND order_type = 'OCO' "
                "AND time_in_force = 'GTC' "
                "AND is_supported = TRUE"
            )
        )
    ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_crypto_oco_unsupported(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT is_supported, notes FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'CRYPTO' "
                "AND order_type = 'OCO' "
                "AND time_in_force = 'GTC'"
            )
        )
    ).one()

    assert row.is_supported is False
    assert "not supported" in row.notes.lower()
