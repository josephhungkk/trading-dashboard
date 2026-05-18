"""Phase 15b alembic 0052 schema tests."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio]


async def test_crypto_order_book_snapshots_schema(session: AsyncSession) -> None:
    """crypto_order_book_snapshots table exists and accepts a minimal insert."""
    result = await session.execute(
        text(
            """
            INSERT INTO instruments
                (canonical_id, asset_class, primary_exchange, currency, display_name)
            VALUES ('crypto:BTC.USD:test_snap', 'CRYPTO', 'PAXOS', 'USD', 'BTC/USD')
            RETURNING id
            """
        )
    )
    instrument_id = result.scalar_one()
    await session.execute(
        text(
            """
            INSERT INTO crypto_order_book_snapshots
                (instrument_id, source, level, side, price, qty, captured_at)
            VALUES
                (:iid, 'coinbase', 1, 'bid', 100.00, 1.00, now())
            """
        ),
        {"iid": instrument_id},
    )


async def test_crypto_asset_class_enum(session: AsyncSession) -> None:
    """Verify that CRYPTO exists in the instrument_asset_class enum."""
    result = await session.execute(text("SELECT unnest(enum_range(NULL::instrument_asset_class))"))
    values = [r[0] for r in result.fetchall()]
    assert "CRYPTO" in values
