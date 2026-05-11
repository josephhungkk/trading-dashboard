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
        # DB H-2: DATE not TIMESTAMPTZ — prevents conflict-key drift from
        # microsecond-resolution writers (per-day uniqueness is the contract).
        sa.Column("day_start_utc", sa.Date(), nullable=False),
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
    # DB M-4: project staleness_s as float epoch directly so callers don't
    # have to EXTRACT(EPOCH FROM ...) at every read site.
    op.execute("DROP VIEW IF EXISTS v_account_intraday_pnl")
    op.execute("""
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
            p.account_id AS account_id,
            p.day_start_utc AS day_start_utc,
            p.realized_today AS realized,
            p.unrealized AS unrealized,
            p.summary_updated_at AS summary_updated_at,
            EXTRACT(EPOCH FROM (now() - p.summary_updated_at))::float AS staleness_s
        FROM pnl_intraday p
        WHERE p.day_start_utc = (now() AT TIME ZONE 'UTC')::date
    """)

    # DB H-1: CONCURRENTLY required for prod safety (spec mandate).
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction; switch the
    # bind to autocommit isolation for this one DDL.
    bind = op.get_bind()
    bind = bind.execution_options(isolation_level="AUTOCOMMIT")
    bind.execute(
        sa.text(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_risk_decisions_verdict_time "
            "ON risk_decisions (verdict, evaluated_at DESC)"
        )
    )

    # DB M-3: prune helper rejects retain_days < 1. Callers MUST pass days >= 30
    # (spec retention floor); the function itself enforces only the lower hard
    # bound — minimum-30-days is a code-level contract at call sites.
    op.execute("""
        CREATE OR REPLACE FUNCTION prune_risk_decisions_allow(retain_days int)
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE
            deleted_count bigint;
        BEGIN
            IF retain_days < 1 THEN
                RAISE EXCEPTION 'retain_days must be >= 1';
            END IF;
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
    # DB L-2: downgrade stub keeps query-shape compat (staleness_s + summary_updated_at).
    op.execute("""
        CREATE OR REPLACE VIEW v_account_intraday_pnl AS
        SELECT
            ba.id AS account_id,
            (now() AT TIME ZONE 'UTC')::date AS day_start_utc,
            0::NUMERIC(20, 8) AS realized,
            0::NUMERIC(20, 8) AS unrealized,
            NULL::timestamptz AS summary_updated_at,
            0.0::float AS staleness_s
        FROM broker_accounts ba
    """)
    op.drop_table("pnl_intraday")
