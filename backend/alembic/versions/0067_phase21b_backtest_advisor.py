"""Phase 21b — backtest advisor_config column + backtest_advisor_decisions table

Revision ID: 0067
Down Revision: 0066
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE backtests ADD COLUMN advisor_config JSONB"
    ))
    op.execute(text("""
        CREATE TABLE backtest_advisor_decisions (
            id              BIGSERIAL PRIMARY KEY,
            backtest_id     UUID NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
            bar_index       INT NOT NULL,
            canonical_id    TEXT NOT NULL,
            intent          JSONB NOT NULL,
            verdict         TEXT NOT NULL CHECK (verdict IN ('approve','veto','fail_open')),
            reasoning       TEXT NOT NULL,
            latency_ms      INT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX backtest_advisor_decisions_backtest_id_idx "
        "ON backtest_advisor_decisions (backtest_id)"
    ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS backtest_advisor_decisions"))
    op.execute(text("ALTER TABLE backtests DROP COLUMN IF EXISTS advisor_config"))
