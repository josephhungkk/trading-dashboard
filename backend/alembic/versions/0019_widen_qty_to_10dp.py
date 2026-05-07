"""Widen qty columns to 10 decimal places.

Revision ID: 0019_widen_qty_to_10dp
Revises: 0018_pk_widen_asset_class
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0019_widen_qty_to_10dp"
down_revision = "0018_pk_widen_asset_class"
branch_labels = None
depends_on = None


QTY_COLUMNS = (
    ("positions", "qty"),
    ("orders", "qty"),
    ("orders", "filled_qty"),
    ("order_events", "filled_qty"),
    ("fills", "qty"),
    ("pending_fills", "qty"),
)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM information_schema.columns
                  WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    )


def _lock_table(table_name: str) -> None:
    op.execute(text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))


def _alter_column_type(table_name: str, column_name: str, precision: int, scale: int) -> None:
    op.execute(
        text(
            f"ALTER TABLE {table_name} "
            f"ALTER COLUMN {column_name} TYPE NUMERIC({precision}, {scale})"
        )
    )


def upgrade() -> None:
    for table_name, column_name in QTY_COLUMNS:
        _lock_table(table_name)
        _alter_column_type(table_name, column_name, 20, 10)

    _lock_table("order_events")
    if _column_exists("order_events", "fill_qty"):
        _alter_column_type("order_events", "fill_qty", 20, 10)


def downgrade() -> None:
    checks = [
        f"SELECT 1 FROM {table_name} WHERE {column_name} IS NOT NULL "
        f"AND {column_name} != trunc({column_name}, 8) LIMIT 1"
        for table_name, column_name in QTY_COLUMNS
    ]
    if _column_exists("order_events", "fill_qty"):
        checks.append(
            "SELECT 1 FROM order_events WHERE fill_qty IS NOT NULL "
            "AND fill_qty != trunc(fill_qty, 8) LIMIT 1"
        )

    has_too_precise_qty = bool(
        op.get_bind().execute(text(f"SELECT EXISTS ({' UNION ALL '.join(checks)})")).scalar()
    )
    if has_too_precise_qty:
        raise RuntimeError("Cannot downgrade: rows with >8dp qty values exist")

    for table_name, column_name in QTY_COLUMNS:
        _lock_table(table_name)
        _alter_column_type(table_name, column_name, 20, 8)

    _lock_table("order_events")
    if _column_exists("order_events", "fill_qty"):
        _alter_column_type("order_events", "fill_qty", 20, 8)
