"""Phase 7a C1 — Alembic 0008 adds account_hash column + partial index.

These tests exercise the migration against the test DB. They depend on the
test DB already having migrations 0001-0007 applied (the autouse
_apply_migrations fixture in tests/conftest.py runs `alembic upgrade head`
session-wide, so by the time these tests run, head includes 0008).
"""

from sqlalchemy import inspect, text


def test_alembic_0008_adds_account_hash_column():
    """After the autouse migration to head, broker_accounts has account_hash."""
    import asyncio

    from app.core.db import engine

    async def check():
        async with engine.connect() as conn:
            cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_columns("broker_accounts")
                }
            )
        return cols

    cols = asyncio.run(check())
    assert "account_hash" in cols


def test_alembic_0008_partial_index_exists():
    import asyncio

    from app.core.db import engine

    async def query():
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'idx_broker_accounts_schwab_hash'"
                )
            )
            return result.fetchall()

    rows = asyncio.run(query())
    assert len(rows) == 1
    indexdef = rows[0][0]
    assert "WHERE" in indexdef
    assert "account_hash IS NOT NULL" in indexdef
