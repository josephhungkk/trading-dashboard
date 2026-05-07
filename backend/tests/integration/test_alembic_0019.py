"""Phase 8c T-0.9 -- verify 0019 widens qty columns to 10dp."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.config import settings

QTY_COLUMNS = (
    ("orders", "qty"),
    ("orders", "filled_qty"),
    ("order_events", "fill_qty"),
)


def _sync_url() -> str:
    return settings.database_url.replace("+asyncpg", "")


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", _sync_url())
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


def test_downgrade_fail_closed() -> None:
    engine = create_engine(_sync_url())
    cfg = _alembic_config()
    order_id = uuid4()
    account_number = f"UTEST_0019_DOWN_{uuid4().hex}"

    try:
        with engine.begin() as conn:
            account_id = conn.execute(
                text(
                    """
                    INSERT INTO broker_accounts (
                      broker_id, account_number, mode, gateway_label, currency_base, last_seen_via
                    )
                    VALUES ('ibkr', :account_number, 'paper', 'test', 'USD', 'test')
                    RETURNING id
                    """
                ),
                {"account_number": account_number},
            ).scalar_one()
            conn.execute(
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

        with pytest.raises(RuntimeError, match="Cannot downgrade: rows with >8dp qty values exist"):
            command.downgrade(cfg, "-1")
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM orders WHERE id = :id"), {"id": order_id})
            conn.execute(
                text("DELETE FROM broker_accounts WHERE account_number = :account_number"),
                {"account_number": account_number},
            )
        command.upgrade(cfg, "head")
        engine.dispose()
