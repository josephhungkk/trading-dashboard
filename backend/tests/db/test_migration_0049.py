"""Migration 0049 — combo_orders, order_legs, orders.combo_id, risk_limits/decisions widening."""

import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_combo_orders_table_exists():
    from app.core.db import engine

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "combo_orders" in tables


@pytest.mark.asyncio
async def test_order_legs_table_exists():
    from app.core.db import engine

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "order_legs" in tables


@pytest.mark.asyncio
async def test_orders_combo_id_column_exists():
    from app.core.db import engine

    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("orders")}
        )
    assert "combo_id" in cols


@pytest.mark.asyncio
async def test_risk_limits_combo_columns_exist():
    from app.core.db import engine

    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("risk_limits")}
        )
    assert "max_combo_loss_native" in cols
    assert "max_combo_net_delta" in cols
    assert "combo_legout_autoclose" in cols


@pytest.mark.asyncio
async def test_risk_decisions_side_check_includes_combo():
    from app.core.db import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'risk_decisions_side_check'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert "combo" in row[0]


@pytest.mark.asyncio
async def test_risk_decisions_attempt_kind_check_includes_combo_events():
    from app.core.db import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'risk_decisions_attempt_kind_check'"
            )
        )
        row = result.fetchone()
    assert row is not None
    assert "combo_preview" in row[0]
    assert "combo_place" in row[0]
    assert "combo_autoclose" in row[0]
