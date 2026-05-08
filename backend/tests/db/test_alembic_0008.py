"""Phase 7a C1 — Alembic 0008 adds account_hash column + partial index.

These tests exercise the migration against the test DB. They depend on the
test DB already having migrations 0001-0007 applied (the autouse
_apply_migrations fixture in tests/conftest.py runs `alembic upgrade head`
session-wide, so by the time these tests run, head includes 0008).
"""

import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_alembic_0008_adds_account_hash_column():
    """After the autouse migration to head, broker_accounts has account_hash."""
    from app.core.db import engine

    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("broker_accounts")}
        )
    assert "account_hash" in cols


@pytest.mark.asyncio
async def test_alembic_0008_partial_index_exists():
    """Verify the schwab account_hash partial index exists in some form.

    0008 originally created `idx_broker_accounts_schwab_hash` (non-unique
    partial index). 0030 dropped it and replaced with the unique
    `uq_broker_accounts_schwab_hash`. Either is acceptable — this asserts
    the partial-index invariant (`WHERE account_hash IS NOT NULL`) holds
    on whichever name currently exists.
    """
    from app.core.db import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname IN ("
                "  'idx_broker_accounts_schwab_hash',"
                "  'uq_broker_accounts_schwab_hash'"
                ")"
            )
        )
        rows = result.fetchall()
    assert len(rows) >= 1, "no schwab account_hash partial index found"
    indexdef = rows[0][0]
    assert "WHERE" in indexdef
    assert "account_hash IS NOT NULL" in indexdef
