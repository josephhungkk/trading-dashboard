"""Phase 8c T-B-cr.2 -- verify Alpaca CRYPTO BRACKET negative capability."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_alpaca_crypto_bracket_unsupported(db_session: AsyncSession) -> None:
    """Alpaca CRYPTO BRACKET/DAY should be explicitly unsupported after 0021-cr."""
    row = (
        await db_session.execute(
            text(
                "SELECT is_supported, notes FROM broker_order_capability "
                "WHERE broker_id = 'alpaca' "
                "AND asset_class = 'CRYPTO' "
                "AND order_type = 'BRACKET' "
                "AND time_in_force = 'DAY'"
            )
        )
    ).one()

    assert row.is_supported is False
    assert "not supported" in row.notes.lower()
