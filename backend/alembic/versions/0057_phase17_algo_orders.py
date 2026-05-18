"""phase17 algo orders — orders columns + broker_algo_capability table + seed.

Revision ID: 0057
Revises: 0056_phase16_fixups
Create Date: 2026-05-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056_phase16_fixups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # orders table — two nullable columns
    op.add_column("orders", sa.Column("algo_strategy", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("algo_params", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "orders_algo_strategy_check",
        "orders",
        "algo_strategy IN ('ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE','ICEBERG','RESERVE','DARK_ICE')",
    )

    # broker_algo_capability table
    op.create_table(
        "broker_algo_capability",
        sa.Column("broker_id", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("algo_strategy", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("broker_id", "asset_class", "algo_strategy"),
        sa.CheckConstraint(
            "broker_id IN ('ibkr','futu','schwab','alpaca')",
            name="broker_algo_capability_broker_id_valid",
        ),
        sa.CheckConstraint(
            "asset_class IN ('STOCK','ETF','OPTION','FUTURE','FOREX','BOND','CFD','CRYPTO','MUTUAL_FUND')",
            name="broker_algo_capability_asset_class_valid",
        ),
        sa.CheckConstraint(
            "algo_strategy IN ('ADAPTIVE','TWAP','VWAP','ARRIVAL_PRICE','ICEBERG','RESERVE','DARK_ICE')",
            name="broker_algo_capability_algo_strategy_valid",
        ),
        sa.CheckConstraint(
            r"notes ~ '^[\x20-\x7E]*$' AND length(notes) <= 256",
            name="broker_algo_capability_notes_printable_ascii",
        ),
    )

    # Seed: only enabled rows (absent = unsupported)
    rows = [
        # STOCK / ETF — all 7 strategies
        *[
            ("ibkr", ac, strat)
            for ac in ("STOCK", "ETF")
            for strat in ("ADAPTIVE", "TWAP", "VWAP", "ARRIVAL_PRICE", "ICEBERG", "RESERVE", "DARK_ICE")
        ],
        # OPTION — ADAPTIVE + ICEBERG only
        ("ibkr", "OPTION", "ADAPTIVE"),
        ("ibkr", "OPTION", "ICEBERG"),
        # FUTURE — all except DARK_ICE
        *[
            ("ibkr", "FUTURE", strat)
            for strat in ("ADAPTIVE", "TWAP", "VWAP", "ARRIVAL_PRICE", "ICEBERG", "RESERVE")
        ],
        # FOREX — ADAPTIVE + TWAP + VWAP
        ("ibkr", "FOREX", "ADAPTIVE"),
        ("ibkr", "FOREX", "TWAP"),
        ("ibkr", "FOREX", "VWAP"),
    ]
    op.bulk_insert(
        sa.table(
            "broker_algo_capability",
            sa.column("broker_id", sa.String),
            sa.column("asset_class", sa.String),
            sa.column("algo_strategy", sa.String),
        ),
        [{"broker_id": b, "asset_class": a, "algo_strategy": s} for b, a, s in rows],
    )


def downgrade() -> None:
    op.drop_table("broker_algo_capability")
    op.drop_constraint("orders_algo_strategy_check", "orders", type_="check")
    op.drop_column("orders", "algo_params")
    op.drop_column("orders", "algo_strategy")
