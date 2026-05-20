"""Round-trip test for migration 0073 (Phase 23a CGT schema)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_0073_tables_exist(engine: AsyncEngine) -> None:
    tables = [
        "hmrc_fx_rates",
        "broker_statements",
        "cgt_class_links",
        "cgt_loss_carry_forward",
        "tax_events",
        "s104_pool",
        "short_obligations",
        "derivative_positions",
        "s104_pool_events",
        "cgt_disposals",
        "income_events",
    ]
    async with engine.connect() as conn:
        for table in tables:
            row = await conn.execute(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = :t"),
                {"t": table},
            )
            assert row.scalar_one() == 1, f"table {table} missing"


@pytest.mark.asyncio
async def test_0073_fills_enriched(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='fills' AND column_name IN "
                "('instrument_id','side','bot_id')"
            )
        )
        cols = {r[0] for r in row.fetchall()}
        assert cols == {"instrument_id", "side", "bot_id"}


@pytest.mark.asyncio
async def test_0073_s104_pool_qty_check(engine: AsyncEngine) -> None:
    """qty < 0 must be rejected by CHECK constraint."""
    from sqlalchemy.exc import DBAPIError

    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match=r"chk|check|violates"):
            await conn.execute(
                text("""
                INSERT INTO s104_pool (account_id, instrument_id, qty, total_cost_gbp,
                                       last_updated_at)
                SELECT id, 1, -1, 0, now() FROM broker_accounts LIMIT 1
            """)
            )
            await conn.commit()
