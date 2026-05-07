"""Phase 8c T-C.6 -- verify Alpaca CRYPTO capability flip."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_alpaca_crypto_rows_supported(db_session: AsyncSession) -> None:
    """Alpaca CRYPTO capability rows should be enabled after 0020a."""
    n = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'CRYPTO' "
                "AND is_supported = TRUE"
            )
        )
    ).scalar_one()
    # Conservative empirical PASS set: MARKET (DAY/GTC) + LIMIT (DAY/GTC).
    assert n >= 4
