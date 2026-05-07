"""Phase 9 Task 3 — verify tick_size column on instruments after migration 0023b."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_0023b_tick_size_column_exists_and_nullable(
    db_session: AsyncSession,
) -> None:
    """instruments.tick_size must exist as NUMERIC(20,8) and be nullable."""
    row = (
        await db_session.execute(
            text(
                """
                SELECT column_name,
                       is_nullable,
                       data_type,
                       numeric_precision,
                       numeric_scale
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name   = 'instruments'
                   AND column_name  = 'tick_size'
                """
            )
        )
    ).first()
    assert row is not None, "instruments.tick_size column missing after 0023b"
    assert row.is_nullable == "YES", "instruments.tick_size must be nullable"
    assert row.data_type == "numeric", (
        f"instruments.tick_size expected numeric, got {row.data_type!r}"
    )
    assert row.numeric_precision == 20, f"expected precision 20, got {row.numeric_precision!r}"
    assert row.numeric_scale == 8, f"expected scale 8, got {row.numeric_scale!r}"


async def test_0023b_tick_size_accepts_null(db_session: AsyncSession) -> None:
    """instruments.tick_size accepts NULL (default for new rows)."""
    inst_id = (
        await db_session.execute(
            text(
                """
                INSERT INTO instruments
                    (canonical_id, asset_class, primary_exchange, currency, display_name)
                VALUES ('TEST0023B.US', 'STOCK', 'NASDAQ', 'USD', 'Test 0023b null tick')
                RETURNING id
                """
            )
        )
    ).scalar_one()
    await db_session.flush()

    tick = (
        await db_session.execute(
            text("SELECT tick_size FROM instruments WHERE id = :id"),
            {"id": inst_id},
        )
    ).scalar()
    assert tick is None, f"Expected NULL tick_size, got {tick!r}"

    await db_session.execute(text("DELETE FROM instruments WHERE id = :id"), {"id": inst_id})
    await db_session.flush()


async def test_0023b_tick_size_accepts_value(db_session: AsyncSession) -> None:
    """instruments.tick_size stores and returns a NUMERIC(20,8) value correctly."""
    inst_id = (
        await db_session.execute(
            text(
                """
                INSERT INTO instruments
                    (canonical_id, asset_class, primary_exchange, currency,
                     display_name, tick_size)
                VALUES ('TEST0023B2.US', 'CRYPTO', 'COINBASE', 'USD',
                        'Test 0023b tick value', 0.01)
                RETURNING id
                """
            )
        )
    ).scalar_one()
    await db_session.flush()

    tick = (
        await db_session.execute(
            text("SELECT tick_size FROM instruments WHERE id = :id"),
            {"id": inst_id},
        )
    ).scalar()
    assert tick is not None, "tick_size should not be NULL after explicit insert"
    assert float(tick) == pytest.approx(0.01), f"Expected tick_size 0.01, got {tick!r}"

    await db_session.execute(text("DELETE FROM instruments WHERE id = :id"), {"id": inst_id})
    await db_session.flush()
