"""Phase 9 Task 2 — verify instrument_id resolver columns after migration 0023a."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_0023a_positions_instrument_id_column_nullable(
    db_session: AsyncSession,
) -> None:
    """positions.instrument_id must exist and be nullable."""
    row = (
        await db_session.execute(
            text(
                """
                SELECT is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name   = 'positions'
                   AND column_name  = 'instrument_id'
                """
            )
        )
    ).first()
    assert row is not None, "positions.instrument_id column missing after 0023a"
    assert row.is_nullable == "YES", "positions.instrument_id must be nullable"


async def test_0023a_watchlist_entries_instrument_id_column_nullable(
    db_session: AsyncSession,
) -> None:
    """watchlist_entries.instrument_id must exist and be nullable."""
    row = (
        await db_session.execute(
            text(
                """
                SELECT is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name   = 'watchlist_entries'
                   AND column_name  = 'instrument_id'
                """
            )
        )
    ).first()
    assert row is not None, "watchlist_entries.instrument_id column missing after 0023a"
    assert row.is_nullable == "YES", "watchlist_entries.instrument_id must be nullable"


async def test_0023a_partial_indexes_exist(db_session: AsyncSession) -> None:
    """Both partial indexes must be present after 0023a."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname IN (
                       'positions_instrument_idx',
                       'watchlist_entries_instrument_idx'
                   )
                """
            )
        )
    ).all()
    found = {r.indexname for r in rows}
    assert "positions_instrument_idx" in found, "positions_instrument_idx partial index missing"
    assert "watchlist_entries_instrument_idx" in found, (
        "watchlist_entries_instrument_idx partial index missing"
    )


async def test_0023a_backfill_links_position_via_canonical_id(
    db_session: AsyncSession,
) -> None:
    """Backfill UPDATE links a position to its instrument via canonical_id."""
    # Insert a test instrument
    inst_id = (
        await db_session.execute(
            text(
                """
                INSERT INTO instruments
                    (canonical_id, asset_class, primary_exchange, currency, display_name)
                VALUES ('TEST0023A.US', 'STOCK', 'NASDAQ', 'USD', 'Test 0023a')
                RETURNING id
                """
            )
        )
    ).scalar_one()
    await db_session.flush()

    # Resolve any available broker_accounts row for the FK
    acct_id = (await db_session.execute(text("SELECT id FROM broker_accounts LIMIT 1"))).scalar()

    if acct_id is None:
        pytest.skip("No broker_accounts row available — skipping backfill subtest")

    await db_session.execute(
        text(
            """
            INSERT INTO positions
                (account_id, conid, qty, avg_cost, currency, canonical_id, symbol)
            VALUES (:acct, 'TEST0023A', 1, 100, 'USD', 'TEST0023A.US', 'TEST0023A')
            """
        ),
        {"acct": acct_id},
    )
    await db_session.flush()

    # Simulate the migration backfill UPDATE
    await db_session.execute(
        text(
            """
            UPDATE positions p
               SET instrument_id = i.id
              FROM instruments i
             WHERE i.canonical_id = p.canonical_id
               AND p.canonical_id IS NOT NULL
               AND p.instrument_id IS NULL
            """
        )
    )
    await db_session.flush()

    linked = (
        await db_session.execute(
            text("SELECT instrument_id FROM positions WHERE canonical_id = 'TEST0023A.US'")
        )
    ).scalar()
    assert linked == inst_id, f"Backfill did not link position: got {linked!r}, want {inst_id!r}"

    # Cleanup
    await db_session.execute(text("DELETE FROM positions WHERE conid = 'TEST0023A'"))
    await db_session.execute(text("DELETE FROM instruments WHERE canonical_id = 'TEST0023A.US'"))
    await db_session.flush()
