"""add earnings_events, earnings_hooks, hook_audit; widen attempt_kind check

Revision ID: 0060
Revises: 0059
Create Date: 2026-05-19
"""

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "earnings_events",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_id", sa.Text(), nullable=False),
        sa.Column("announced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("announced_date", sa.Date(), nullable=False),
        sa.Column("time_of_day", sa.Text(), nullable=True),
        sa.Column("eps_estimate", sa.Numeric(20, 8), nullable=True),
        sa.Column("eps_actual", sa.Numeric(20, 8), nullable=True),
        sa.Column("revenue_estimate", sa.Numeric(20, 8), nullable=True),
        sa.Column("revenue_actual", sa.Numeric(20, 8), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "captured_at",
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
        sa.CheckConstraint(
            "time_of_day IN ('before_open', 'after_close', 'during_market', 'unknown')",
            name="earnings_events_time_of_day_check",
        ),
        sa.CheckConstraint(
            "source IN ('nasdaq_api', 'finnhub_api', 'manual')",
            name="earnings_events_source_check",
        ),
        sa.UniqueConstraint("instrument_id", "announced_date", name="uq_earnings_instrument_date"),
    )
    op.create_index("ix_earnings_events_instrument_id", "earnings_events", ["instrument_id"])
    op.create_index("ix_earnings_events_announced_date", "earnings_events", ["announced_date"])

    op.create_table(
        "earnings_hooks",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.UUID(),
            sa.ForeignKey("broker_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jwt_subject", sa.Text(), nullable=False),
        sa.Column("hook_type", sa.Text(), nullable=False),
        sa.Column("minutes_before", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("bot_id", sa.UUID(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "hook_type IN ('auto_flat', 'auto_pause_bot')",
            name="earnings_hooks_type_check",
        ),
        sa.CheckConstraint("minutes_before >= 10", name="earnings_hooks_minutes_before_check"),
    )

    op.create_table(
        "hook_audit",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "hook_id",
            sa.UUID(),
            sa.ForeignKey("earnings_hooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("earnings_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fired_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('placed', 'skipped_no_position', 'failed', 'failed_kill_switch')",
            name="hook_audit_outcome_check",
        ),
        sa.UniqueConstraint("hook_id", "event_id", name="uq_hook_audit_hook_event"),
    )

    op.execute("ALTER TABLE risk_decisions DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check")
    op.execute(
        """
        ALTER TABLE risk_decisions
        ADD CONSTRAINT risk_decisions_attempt_kind_check
        CHECK (attempt_kind IN (
            'preview', 'place', 'modify', 'place_order', 'modify_order',
            'combo_preview', 'combo_place', 'combo_autoclose',
            'telegram', 'telegram_confirm',
            'earnings_hook_flat'
        ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE risk_decisions DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check")
    op.execute(
        """
        ALTER TABLE risk_decisions
        ADD CONSTRAINT risk_decisions_attempt_kind_check
        CHECK (attempt_kind IN (
            'preview', 'place', 'modify', 'place_order', 'modify_order',
            'combo_preview', 'combo_place', 'combo_autoclose',
            'telegram', 'telegram_confirm'
        ))
        """
    )
    op.drop_table("hook_audit")
    op.drop_table("earnings_hooks")
    op.drop_index("ix_earnings_events_announced_date", table_name="earnings_events")
    op.drop_index("ix_earnings_events_instrument_id", table_name="earnings_events")
    op.drop_table("earnings_events")
