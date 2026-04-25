"""Tests for Alembic migration 0002_broker_accounts (Phase 4 Task 29)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
async def test_broker_accounts_table_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='broker_accounts'"
            )
        )
        assert result.scalar_one() == "broker_accounts"


@pytest.mark.asyncio
async def test_all_columns_present_with_correct_types(engine: AsyncEngine) -> None:
    """Spec section 4.4 columns + last_seen_via (C1 race-free soft-delete)."""

    expected = {
        "id": ("uuid", "NO"),
        "broker_id": ("USER-DEFINED", "NO"),
        "account_number": ("text", "NO"),
        "alias": ("text", "YES"),
        "mode": ("USER-DEFINED", "NO"),
        "gateway_label": ("text", "NO"),
        "currency_base": ("text", "NO"),
        "display_order": ("integer", "NO"),
        "first_seen_at": ("timestamp with time zone", "NO"),
        "last_seen_at": ("timestamp with time zone", "NO"),
        "last_seen_via": ("text", "NO"),
        "deleted_at": ("timestamp with time zone", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    }

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='broker_accounts'"
            )
        )
        actual = {row.column_name: (row.data_type, row.is_nullable) for row in result}

    assert actual == expected


@pytest.mark.asyncio
async def test_partial_index_exists_with_where_clause(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND indexname='ix_broker_accounts_active'"
            )
        )
        indexdef = result.scalar_one_or_none()

    assert indexdef is not None, "ix_broker_accounts_active missing"
    assert "broker_id" in indexdef
    assert "mode" in indexdef
    assert "deleted_at IS NULL" in indexdef


@pytest.mark.asyncio
async def test_enum_types_exist_with_correct_values(engine: AsyncEngine) -> None:
    """broker_id_enum and trading_mode_enum must be present + populated."""

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT t.typname, e.enumlabel "
                "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
                "WHERE t.typname IN ('broker_id_enum', 'trading_mode_enum') "
                "ORDER BY t.typname, e.enumsortorder"
            )
        )
        rows = [(r.typname, r.enumlabel) for r in result]

    assert ("broker_id_enum", "ibkr") in rows
    assert ("broker_id_enum", "futu") in rows
    assert ("broker_id_enum", "schwab") in rows
    assert ("trading_mode_enum", "live") in rows
    assert ("trading_mode_enum", "paper") in rows


@pytest.mark.asyncio
async def test_natural_unique_constraint_blocks_duplicate(engine: AsyncEngine) -> None:
    """broker_accounts_natural_uq: (broker_id, account_number) must be unique."""

    insert_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, last_seen_via) "
        "VALUES (:b, :a, :m, :g, :c, :v)"
    )
    delete_sql = text("DELETE FROM broker_accounts WHERE broker_id=:b AND account_number=:a")
    params = {
        "b": "ibkr",
        "a": "UTEST_DUP_001",
        "m": "paper",
        "g": "isa-paper",
        "c": "USD",
        "v": "isa-paper",
    }

    try:
        async with engine.begin() as conn:
            await conn.execute(insert_sql, params)

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert_sql, params)
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete_sql, params)


@pytest.mark.asyncio
async def test_default_values_populate(engine: AsyncEngine) -> None:
    """gen_random_uuid + display_order=0 + first_seen_at=now() server defaults."""

    insert_sql = text(
        "INSERT INTO broker_accounts "
        "(broker_id, account_number, mode, gateway_label, currency_base, last_seen_via) "
        "VALUES (:b, :a, :m, :g, :c, :v) "
        "RETURNING id, display_order, first_seen_at, deleted_at"
    )
    delete_sql = text("DELETE FROM broker_accounts WHERE broker_id=:b AND account_number=:a")
    params = {
        "b": "ibkr",
        "a": "UTEST_DEFAULTS_001",
        "m": "live",
        "g": "isa-live",
        "c": "GBP",
        "v": "isa-live",
    }

    try:
        async with engine.begin() as conn:
            row = (await conn.execute(insert_sql, params)).one()

        assert row.id is not None
        assert row.display_order == 0
        assert row.first_seen_at is not None
        assert row.deleted_at is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete_sql, params)


def _run_alembic(action: str, target: str) -> None:
    """Invoke alembic via subprocess. Calling alembic.command directly inside
    a pytest-asyncio test fails because env.py uses asyncio.run() which
    conflicts with the test's already-running event loop."""

    import subprocess

    subprocess.run(
        ["uv", "run", "python", "-m", "alembic", action, target],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_downgrade_then_upgrade_round_trip(engine: AsyncEngine) -> None:
    """Run downgrade -1 then upgrade head; verify the table + enum types are
    dropped and recreated cleanly. Schema returns to head before the test
    finishes so subsequent tests are unaffected."""

    _run_alembic("downgrade", "-1")
    async with engine.connect() as conn:
        tbl = (
            await conn.execute(text("SELECT to_regclass('public.broker_accounts')"))
        ).scalar_one()
        enm = (
            await conn.execute(text("SELECT 1 FROM pg_type WHERE typname='broker_id_enum'"))
        ).scalar_one_or_none()
    assert tbl is None, "broker_accounts table should not exist after downgrade"
    assert enm is None, "broker_id_enum should not exist after downgrade"

    _run_alembic("upgrade", "head")
    async with engine.connect() as conn:
        tbl = (
            await conn.execute(text("SELECT to_regclass('public.broker_accounts')"))
        ).scalar_one()
    assert tbl is not None
