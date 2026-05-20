"""Phase 22b — generated_strategies + bot_strategy_provenance + bots.strategy_class

Revision ID: 0071
Down Revision: 0070
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE generated_strategies (
            id                  BIGSERIAL PRIMARY KEY,
            name                TEXT NOT NULL,
            source_code         TEXT NOT NULL,
            source_hash         TEXT NOT NULL,
            generation_prompt   TEXT NOT NULL,
            prompt_hash         TEXT NOT NULL,
            llm_model           TEXT NOT NULL,
            sandbox_status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (sandbox_status IN ('pending','validated','rejected','promoted')),
            sandbox_error       TEXT,
            backtest_id         UUID REFERENCES backtests(id) ON DELETE SET NULL,
            promoted_bot_id     UUID REFERENCES bots(id) ON DELETE SET NULL,
            approved_by         TEXT,
            approved_at         TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX generated_strategies_sandbox_status_idx"
        " ON generated_strategies (sandbox_status, created_at DESC)"
    ))
    op.execute(text(
        "CREATE INDEX generated_strategies_prompt_hash_idx"
        " ON generated_strategies (prompt_hash)"
    ))

    op.execute(text("""
        CREATE TABLE bot_strategy_provenance (
            id              BIGSERIAL PRIMARY KEY,
            bot_id          UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            strategy_id     BIGINT REFERENCES generated_strategies(id) ON DELETE SET NULL,
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            replaced_at     TIMESTAMPTZ
        )
    """))
    op.execute(text(
        "CREATE INDEX bot_strategy_provenance_bot_id_idx"
        " ON bot_strategy_provenance (bot_id, generated_at DESC)"
    ))

    op.execute(text("ALTER TABLE bots ADD COLUMN strategy_class TEXT"))

    # Widen bots.status check to include paper_pending and vetoed (22b veto window)
    op.execute(text("ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_status_check"))
    op.execute(text(
        "ALTER TABLE bots ADD CONSTRAINT bots_status_check"
        " CHECK (status IN ('stopped','starting','running','pausing','paused','error',"
        "                   'paper_pending','vetoed'))"
    ))


def downgrade() -> None:
    # Restore original status check
    op.execute(text("ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_status_check"))
    op.execute(text(
        "ALTER TABLE bots ADD CONSTRAINT bots_status_check"
        " CHECK (status IN ('stopped','starting','running','pausing','paused','error'))"
    ))
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS strategy_class"))
    op.execute(text("DROP TABLE IF EXISTS bot_strategy_provenance CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS generated_strategies CASCADE"))
