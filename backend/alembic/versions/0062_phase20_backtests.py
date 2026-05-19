"""add backtests, backtest_bar_uploads, backtest_bars tables

Revision ID: 0062
Revises: 0061a
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bot_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.TEXT(), server_default=sa.text("'queued'::text"), nullable=False),
        sa.Column("timeframe", sa.TEXT(), nullable=False),
        sa.Column("canonical_id", sa.TEXT(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("slippage_bps", sa.Numeric(8, 2), nullable=True),
        sa.Column("slippage_atr_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("commission_cfg", sa.JSON(), nullable=False),
        sa.Column("params_snapshot", sa.JSON(), nullable=False),
        sa.Column("params_schema_hash", sa.TEXT(), nullable=True),
        sa.Column("bars_source", sa.TEXT(), nullable=False),
        sa.Column("parent_backtest_id", sa.UUID(), nullable=True),
        sa.Column("progress_pct", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_msg", sa.TEXT(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued','running','done','failed')", name="backtests_status_check"),
        sa.CheckConstraint("bars_source IN ('db','backfill','csv')", name="backtests_bars_source_check"),
        sa.CheckConstraint(
            "(slippage_bps IS NOT NULL AND slippage_atr_pct IS NULL) OR (slippage_bps IS NULL AND slippage_atr_pct IS NOT NULL)",
            name="backtests_slippage_xor",
        ),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_backtest_id"], ["backtests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtests_bot_id_created", "backtests", ["bot_id", sa.text("created_at DESC")])
    op.create_index("ix_backtests_parent_id", "backtests", ["parent_backtest_id"], postgresql_where=sa.text("parent_backtest_id IS NOT NULL"))
    op.create_index("ix_backtests_running_stale", "backtests", ["started_at"], postgresql_where=sa.text("status = 'running'"))

    op.create_table(
        "backtest_bar_uploads",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_id", sa.TEXT(), nullable=False),
        sa.Column("timeframe", sa.TEXT(), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bbu_canonical_tf_uploaded", "backtest_bar_uploads", ["canonical_id", "timeframe", sa.text("uploaded_at DESC")])

    op.create_table(
        "backtest_bars",
        sa.Column("upload_id", sa.UUID(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(20, 8), nullable=True),
        sa.ForeignKeyConstraint(["upload_id"], ["backtest_bar_uploads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("upload_id", "instrument_id", "bucket_start"),
    )
    op.create_index("ix_backtest_bars_instrument", "backtest_bars", ["instrument_id", "bucket_start"])


def downgrade() -> None:
    op.drop_table("backtest_bars")
    op.drop_table("backtest_bar_uploads")
    op.drop_table("backtests")
