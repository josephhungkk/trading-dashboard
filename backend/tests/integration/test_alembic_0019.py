"""Phase 8c T-0.9 -- verify 0019 widens qty columns to 10dp."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.config import settings

QTY_COLUMNS = (
    ("orders", "qty"),
    ("orders", "filled_qty"),
    ("order_events", "fill_qty"),
)


def _alembic_config() -> Config:
    """Alembic Config wired at the +asyncpg URL — env.py uses
    async_engine_from_config so the +asyncpg driver works directly.

    ``config_file_name`` is cleared so Alembic's env.py skips
    ``fileConfig()``; otherwise it resets the root logger and
    caplog handlers stop receiving records for subsequent tests
    (matches the same guard in tests/conftest.py::_apply_migrations).
    """
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


@pytest.mark.asyncio
async def test_qty_columns_are_10dp(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                """
                SELECT table_name, column_name, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                    (table_name = 'orders' AND column_name IN ('qty', 'filled_qty'))
                    OR (table_name = 'order_events' AND column_name = 'fill_qty')
                  )
                """
            )
        )
    ).all()
    observed = {(row.table_name, row.column_name): row for row in rows}

    for table_name, column_name in QTY_COLUMNS:
        row = observed.get((table_name, column_name))
        if row is None and (table_name, column_name) == ("order_events", "fill_qty"):
            continue
        assert row is not None
        assert row.numeric_precision == 20
        assert row.numeric_scale == 10

    account_id = (
        await db_session.execute(
            text(
                """
                INSERT INTO broker_accounts (
                  broker_id, account_number, mode, gateway_label, currency_base, last_seen_via
                )
                VALUES ('ibkr', :account_number, 'paper', 'test', 'USD', 'test')
                RETURNING id
                """
            ),
            {"account_number": f"UTEST_0019_{uuid4().hex}"},
        )
    ).scalar_one()
    order_id = uuid4()
    qty = Decimal("0.0000000001")

    await db_session.execute(
        text(
            """
            INSERT INTO orders (
              id, account_id, client_order_id, conid, symbol, side, order_type, tif,
              qty, filled_qty, status, notional
            )
            VALUES (
              :id, :account_id, :client_order_id, '265598', 'AAPL', 'BUY', 'MARKET',
              'DAY', :qty, 0, 'pending_submit', 0
            )
            """
        ),
        {
            "id": order_id,
            "account_id": account_id,
            "client_order_id": uuid4(),
            "qty": qty,
        },
    )
    round_trip = (
        await db_session.execute(text("SELECT qty FROM orders WHERE id = :id"), {"id": order_id})
    ).scalar_one()
    assert round_trip == qty


@pytest.mark.asyncio
async def test_downgrade_fail_closed(db_session: AsyncSession) -> None:
    """Verify that 0019 downgrade refuses when rows have >8dp qty values.

    Async rewrite (Phase 11a-CI-debt-2): the original used
    sqlalchemy.create_engine + psycopg2, which isn't in this project's
    dependency set. The migration's downgrade hook itself runs through
    alembic.command.downgrade (sync, with its own DBAPI connection), so
    we drive that part on a thread via asyncio.to_thread.
    """
    import asyncio

    cfg = _alembic_config()
    order_id = uuid4()
    account_number = f"UTEST_0019_DOWN_{uuid4().hex}"

    # Insert a row with 9dp qty to force the downgrade check to fail.
    await db_session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              broker_id, account_number, mode, gateway_label, currency_base, last_seen_via
            )
            VALUES ('ibkr', :account_number, 'paper', 'test', 'USD', 'test')
            """
        ),
        {"account_number": account_number},
    )
    account_id = (
        await db_session.execute(
            text("SELECT id FROM broker_accounts WHERE account_number = :a"),
            {"a": account_number},
        )
    ).scalar_one()
    await db_session.execute(
        text(
            """
            INSERT INTO orders (
              id, account_id, client_order_id, conid, symbol, side, order_type, tif,
              qty, filled_qty, status, notional
            )
            VALUES (
              :id, :account_id, :client_order_id, '265598', 'AAPL', 'BUY',
              'MARKET', 'DAY', :qty, 0, 'pending_submit', 0
            )
            """
        ),
        {
            "id": order_id,
            "account_id": account_id,
            "client_order_id": uuid4(),
            "qty": Decimal("0.0000000001"),
        },
    )
    await db_session.commit()

    try:
        # Target 0018 explicitly — `-1` from current head (0042) would just
        # walk back one revision (to 0041), not all the way to 0018 where
        # the 0019 downgrade hook lives.
        with pytest.raises(RuntimeError, match="Cannot downgrade: rows with >8dp qty values exist"):
            await asyncio.to_thread(command.downgrade, cfg, "0018_pk_widen_asset_class")
    finally:
        await db_session.execute(text("DELETE FROM orders WHERE id = :id"), {"id": order_id})
        await db_session.execute(
            text("DELETE FROM broker_accounts WHERE account_number = :a"),
            {"a": account_number},
        )
        await db_session.commit()
        await asyncio.to_thread(command.upgrade, cfg, "head")
