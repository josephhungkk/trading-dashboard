"""phase 7b.1: instruments + symbol_aliases for streaming quote engine.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    asset_class_enum = postgresql.ENUM(
        "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "FOREX", "CRYPTO",
        name="instrument_asset_class",
        create_type=True,
    )
    asset_class_enum.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "instruments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("canonical_id", sa.Text, nullable=False, unique=True),
        sa.Column(
            "asset_class",
            postgresql.ENUM(name="instrument_asset_class", create_type=False),
            nullable=False,
        ),
        sa.Column("primary_exchange", sa.Text, nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("display_name", sa.Text, nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("instruments_asset_class_idx", "instruments", ["asset_class"])
    op.create_index("instruments_exchange_idx", "instruments", ["primary_exchange"])
    op.execute(
        "COMMENT ON TABLE instruments IS "
        "'Phase 7b.1 — canonical security registry. One row per "
        "(asset_class, symbol, country); meta JSONB carries asset-class-specific "
        "extensions (option strike/expiry, future contract_month, etc.) without "
        "further migrations.'"
    )

    op.create_table(
        "symbol_aliases",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("raw_symbol", sa.Text, nullable=False),
        sa.Column(
            "instrument_id",
            sa.BigInteger,
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "meta",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("source", "raw_symbol"),
    )
    op.create_index("symbol_aliases_instrument_idx", "symbol_aliases", ["instrument_id"])
    op.execute(
        "COMMENT ON TABLE symbol_aliases IS "
        "'Phase 7b.1 — per-source symbol resolution. Composite PK (source, "
        "raw_symbol) eliminates collision risk; instrument_id FK CASCADE deletes "
        "aliases when their canonical instrument is removed.'"
    )


def downgrade() -> None:
    op.drop_index("symbol_aliases_instrument_idx", "symbol_aliases")
    op.drop_table("symbol_aliases")
    op.drop_index("instruments_exchange_idx", "instruments")
    op.drop_index("instruments_asset_class_idx", "instruments")
    op.drop_table("instruments")
    postgresql.ENUM(name="instrument_asset_class").drop(op.get_bind(), checkfirst=False)
