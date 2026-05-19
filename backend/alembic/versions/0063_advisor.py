"""Phase 21a: LLM Advisor — bot_advisor_decisions table, bots.advisor_config,
   bot_accounts.advisor_config_override, widen bot_runs_stop_reason_check.

Audit rows not cascaded on bot deletion; ops must archive then nullify.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen bot_runs.stop_reason CHECK to include advisor_auto_pause
    op.drop_constraint("bot_runs_stop_reason_check", "bot_runs", type_="check")
    op.create_check_constraint(
        "bot_runs_stop_reason_check",
        "bot_runs",
        "stop_reason IN ('manual','error','daily_loss_cap','kill_switch','advisor_auto_pause')",
    )

    # 2. advisor_config JSONB column on bots (NOT NULL, default OFF)
    op.add_column(
        "bots",
        sa.Column(
            "advisor_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{\"mode\":\"OFF\"}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "advisor_config_mode_check",
        "bots",
        "advisor_config ? 'mode' AND advisor_config->>'mode' IN ('OFF','OBSERVE','VETO')",
    )

    # 3. Per-account advisor config override (nullable JSONB; NULL = use bot default)
    op.add_column(
        "bot_accounts",
        sa.Column("advisor_config_override", postgresql.JSONB(), nullable=True),
    )

    # 4. bot_advisor_decisions table
    op.create_table(
        "bot_advisor_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("bot_run_id", sa.UUID(as_uuid=True), nullable=True),  # NO FK: hypertable retention
        sa.Column("account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_id", sa.Text(), nullable=False),
        sa.Column("intent", postgresql.JSONB(), nullable=False),
        sa.Column(
            "context_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("prompt_version", sa.SmallInteger(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "advice_tags",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "fallback_chain",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        # Provenance join — NO FK: ai_completions has composite PK (ts, request_id)
        sa.Column("ai_completion_ts", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ai_completion_request_id", sa.UUID(as_uuid=True), nullable=True),
        # Account-gate outcome — updated after facade returns
        sa.Column(
            "account_gate_outcome",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'not_evaluated'"),
        ),
        sa.Column("account_gate_decision_id", sa.BigInteger(), nullable=True),
        # effective_mode — which AdvisorMode produced this verdict
        sa.Column(
            "effective_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'OFF'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["bot_id"], ["bots.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["broker_accounts.id"], ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "verdict IN ('approve','veto','fail_open')",
            name="bot_advisor_decisions_verdict_check",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="bot_advisor_decisions_confidence_check",
        ),
        sa.CheckConstraint(
            "account_gate_outcome IN ('approved','warned','blocked','not_evaluated','error')",
            name="bot_advisor_decisions_account_gate_outcome_check",
        ),
        sa.CheckConstraint(
            "effective_mode IN ('OFF','OBSERVE','VETO')",
            name="bot_advisor_decisions_effective_mode_check",
        ),
    )
    op.create_index(
        "idx_bot_advisor_decisions_bot_ts",
        "bot_advisor_decisions",
        ["bot_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_bot_advisor_decisions_verdict",
        "bot_advisor_decisions",
        ["verdict", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_bot_advisor_decisions_run",
        "bot_advisor_decisions",
        ["bot_run_id"],
        postgresql_where=sa.text("bot_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_bot_advisor_decisions_run", table_name="bot_advisor_decisions")
    op.drop_index("idx_bot_advisor_decisions_verdict", table_name="bot_advisor_decisions")
    op.drop_index("idx_bot_advisor_decisions_bot_ts", table_name="bot_advisor_decisions")
    op.drop_table("bot_advisor_decisions")
    op.drop_column("bot_accounts", "advisor_config_override")
    op.drop_constraint("advisor_config_mode_check", "bots", type_="check")
    op.drop_column("bots", "advisor_config")
    op.drop_constraint("bot_runs_stop_reason_check", "bot_runs", type_="check")
    op.create_check_constraint(
        "bot_runs_stop_reason_check",
        "bot_runs",
        "stop_reason IN ('manual','error','daily_loss_cap','kill_switch')",
    )
