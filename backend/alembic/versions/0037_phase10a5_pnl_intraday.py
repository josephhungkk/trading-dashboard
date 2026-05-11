"""
Phase 10a.5 §4 (data model)
Revision ID: 0037_phase10a5
Down Revision: 0036_phase10a_risk_engine
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037_phase10a5"
down_revision = "0036_phase10a_risk_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CRIT-1: realized_today MUST come from SUM(positions[*].realized_pnl_today)
    # (proto Position field 7), NEVER Summary.realized_pnl (proto Summary field 3
    # — cumulative for IBKR, would invert the gate).
    op.create_table(
        "pnl_intraday",
        sa.Column(
            "account_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("day_start_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("realized_today", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("unrealized", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column(
            "summary_updated_at", sa.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_label", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_pnl_intraday_currency_iso3"
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "day_start_utc", name="pk_pnl_intraday"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["broker_accounts.id"],
            name="fk_pnl_intraday_account",
            ondelete="CASCADE",
        ),
    )

    # CRIT-2: DROP VIEW first, then CREATE with strict WHERE clause — missing
    # row -> gate returns WARN.
    op.execute("DROP VIEW IF EXISTS v_account_intraday_pnl")
    op.execute("""
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
            p.account_id AS account_id,
            p.day_start_utc AS day_start_utc,
            p.realized_today AS realized,
            p.unrealized AS unrealized,
            p.summary_updated_at AS summary_updated_at,
            (now() - p.summary_updated_at) AS staleness
        FROM pnl_intraday p
        WHERE p.day_start_utc = (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_risk_decisions_verdict_time "
        "ON risk_decisions (verdict, evaluated_at DESC)"
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION prune_risk_decisions_allow(retain_days int)
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            DELETE FROM risk_decisions
            WHERE verdict = 'allow'
              AND evaluated_at < now() - make_interval(days => retain_days);
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS prune_risk_decisions_allow(int)")
    op.execute("DROP INDEX IF EXISTS idx_risk_decisions_verdict_time")
    op.execute("DROP VIEW IF EXISTS v_account_intraday_pnl")
    op.execute("""
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
            ba.id AS account_id,
            (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS day_start_utc,
            0::NUMERIC(20, 8) AS realized,
            0::NUMERIC(20, 8) AS unrealized
        FROM broker_accounts ba
    """)
    op.drop_table("pnl_intraday")
