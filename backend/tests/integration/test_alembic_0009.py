from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def test_0009_creates_instruments_table(db_session) -> None:
    table_exists = (
        await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='instruments'"
            )
        )
    ).scalar()
    columns = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='instruments'"
            )
        )
    ).fetchall()

    assert table_exists == "instruments"
    # 0009 originally created the 9 columns below; later migrations add
    # tick_size etc. — assert ⊇ on the 0009 baseline.
    assert {row.column_name for row in columns} >= {
        "id",
        "canonical_id",
        "asset_class",
        "primary_exchange",
        "currency",
        "display_name",
        "meta",
        "created_at",
        "updated_at",
    }


async def test_0009_creates_symbol_aliases_table(db_session) -> None:
    table_exists = (
        await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='symbol_aliases'"
            )
        )
    ).scalar()
    columns = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='symbol_aliases'"
            )
        )
    ).fetchall()

    assert table_exists == "symbol_aliases"
    assert {row.column_name for row in columns} == {
        "source",
        "raw_symbol",
        "instrument_id",
        "meta",
        "created_at",
    }


async def test_0009_asset_class_enum_has_expected_values(db_session) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT e.enumlabel "
                "FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname='instrument_asset_class' "
                "ORDER BY e.enumsortorder"
            )
        )
    ).fetchall()

    labels = [row.enumlabel for row in rows]
    # Core values from migration 0009; OPTION added in migration 0047 (Phase 12).
    for expected in ["STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "FOREX", "CRYPTO", "OPTION"]:
        assert expected in labels, f"Missing enum label: {expected}"


async def test_0009_canonical_id_is_unique(db_session) -> None:
    insert_sql = text(
        "INSERT INTO instruments "
        "(canonical_id, asset_class, primary_exchange, currency, display_name) "
        "VALUES (:canonical_id, 'STOCK', 'NASDAQ', 'USD', :display_name)"
    )

    await db_session.execute(
        insert_sql,
        {"canonical_id": "TEST0009:DUP", "display_name": "First duplicate"},
    )
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            insert_sql,
            {"canonical_id": "TEST0009:DUP", "display_name": "Second duplicate"},
        )
        await db_session.flush()
    await db_session.rollback()


async def test_0009_symbol_aliases_composite_pk(db_session) -> None:
    instrument_id = (
        await db_session.execute(
            text(
                "INSERT INTO instruments "
                "(canonical_id, asset_class, primary_exchange, currency, display_name) "
                "VALUES ('TEST0009:ALIASPK', 'ETF', 'NYSE', 'USD', 'Alias PK') "
                "RETURNING id"
            )
        )
    ).scalar()
    await db_session.flush()

    insert_alias_sql = text(
        "INSERT INTO symbol_aliases (source, raw_symbol, instrument_id) "
        "VALUES ('futu', 'TEST0009', :instrument_id)"
    )
    await db_session.execute(insert_alias_sql, {"instrument_id": instrument_id})
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(insert_alias_sql, {"instrument_id": instrument_id})
        await db_session.flush()
    await db_session.rollback()


async def test_0009_symbol_aliases_fk_cascade(db_session) -> None:
    instrument_id = (
        await db_session.execute(
            text(
                "INSERT INTO instruments "
                "(canonical_id, asset_class, primary_exchange, currency, display_name) "
                "VALUES ('TEST0009:CASCADE', 'INDEX', 'HKEX', 'HKD', 'Cascade') "
                "RETURNING id"
            )
        )
    ).scalar()
    await db_session.execute(
        text(
            "INSERT INTO symbol_aliases (source, raw_symbol, instrument_id) "
            "VALUES ('schwab', 'CASCADE0009', :instrument_id)"
        ),
        {"instrument_id": instrument_id},
    )
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM instruments WHERE id=:instrument_id"),
        {"instrument_id": instrument_id},
    )
    await db_session.flush()

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM symbol_aliases WHERE instrument_id=:instrument_id"),
            {"instrument_id": instrument_id},
        )
    ).scalar()

    assert count == 0


async def test_0009_indexes_exist(db_session) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' "
                "AND tablename IN ('instruments','symbol_aliases')"
            )
        )
    ).fetchall()

    index_names = {row.indexname for row in rows}
    canonical_id_unique_indexes = [
        row
        for row in rows
        if row.indexdef.startswith("CREATE UNIQUE INDEX")
        and row.indexdef.endswith("ON public.instruments USING btree (canonical_id)")
    ]

    assert {
        "instruments_asset_class_idx",
        "instruments_exchange_idx",
        "symbol_aliases_instrument_idx",
    }.issubset(index_names)
    assert canonical_id_unique_indexes
