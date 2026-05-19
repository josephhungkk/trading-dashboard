"""Phase 21c — advisor attribution outcome columns

Revision ID: 0068
Down Revision: 0067
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE bot_advisor_decisions
            ADD COLUMN attribution_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (attribution_status IN ('pending','partial','complete','bars_unavailable','unresolvable')),
            ADD COLUMN attribution_windows TEXT[]
                CHECK (attribution_windows IS NULL OR attribution_windows <@ ARRAY['15m','1h','4h','eod']::TEXT[]),
            ADD COLUMN outcome_15m_correct BOOL,
            ADD COLUMN outcome_15m_pnl NUMERIC(20,8),
            ADD COLUMN outcome_1h_correct BOOL,
            ADD COLUMN outcome_1h_pnl NUMERIC(20,8),
            ADD COLUMN outcome_4h_correct BOOL,
            ADD COLUMN outcome_4h_pnl NUMERIC(20,8),
            ADD COLUMN outcome_eod_correct BOOL,
            ADD COLUMN outcome_eod_pnl NUMERIC(20,8),
            ADD COLUMN attribution_computed_at TIMESTAMPTZ
    """))
    op.execute(text(
        "CREATE INDEX bot_advisor_decisions_attribution_status_created_at_idx"
        " ON bot_advisor_decisions (attribution_status, created_at DESC)"
        " WHERE attribution_status IN ('pending', 'partial')"
    ))
    op.execute(text(
        "ALTER TABLE bot_orders"
        " ADD COLUMN advisor_decision_id BIGINT"
        " REFERENCES bot_advisor_decisions(id) ON DELETE SET NULL"
    ))
    op.execute(text(
        "CREATE INDEX bot_orders_advisor_decision_id_idx"
        " ON bot_orders (advisor_decision_id)"
        " WHERE advisor_decision_id IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS bot_orders_advisor_decision_id_idx"))
    op.execute(text("ALTER TABLE bot_orders DROP COLUMN IF EXISTS advisor_decision_id"))
    op.execute(text("DROP INDEX IF EXISTS bot_advisor_decisions_attribution_status_created_at_idx"))
    op.execute(text("""
        ALTER TABLE bot_advisor_decisions
            DROP COLUMN IF EXISTS attribution_status,
            DROP COLUMN IF EXISTS attribution_windows,
            DROP COLUMN IF EXISTS outcome_15m_correct,
            DROP COLUMN IF EXISTS outcome_15m_pnl,
            DROP COLUMN IF EXISTS outcome_1h_correct,
            DROP COLUMN IF EXISTS outcome_1h_pnl,
            DROP COLUMN IF EXISTS outcome_4h_correct,
            DROP COLUMN IF EXISTS outcome_4h_pnl,
            DROP COLUMN IF EXISTS outcome_eod_correct,
            DROP COLUMN IF EXISTS outcome_eod_pnl,
            DROP COLUMN IF EXISTS attribution_computed_at
    """))
