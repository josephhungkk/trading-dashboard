"""Phase 21b — param-tuner + shadow bot columns + risk_decisions widening

Revision ID: 0065
Down Revision: 0064
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE bot_param_suggestions (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bot_id                      UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            triggered_by                TEXT NOT NULL CHECK (triggered_by IN ('scheduled','manual')),
            status                      TEXT NOT NULL CHECK (status IN (
                                            'pending','backtesting','ranked',
                                            'approved','rejected','applied','failed')),
            strategy_params_current     JSONB NOT NULL,
            ai_reasoning                TEXT,
            candidates                  JSONB NOT NULL DEFAULT '[]',
            ai_completion_id            BIGINT,
            ai_model                    TEXT,
            ai_prompt_hash              TEXT,
            approved_candidate_index    INT,
            approved_by                 TEXT,
            applied_at                  TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX bot_param_suggestions_bot_id_status_idx "
        "ON bot_param_suggestions (bot_id, status)"
    ))
    op.execute(text("""
        CREATE OR REPLACE FUNCTION set_bot_param_suggestions_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(text("""
        CREATE TRIGGER bot_param_suggestions_updated_at
            BEFORE UPDATE ON bot_param_suggestions
            FOR EACH ROW EXECUTE FUNCTION set_bot_param_suggestions_updated_at()
    """))
    op.execute(text("""
        ALTER TABLE bots
            ADD COLUMN is_shadow                     BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN shadow_of                     UUID REFERENCES bots(id) ON DELETE SET NULL,
            ADD COLUMN shadow_promoted_at            TIMESTAMPTZ,
            ADD COLUMN shadow_comparison_window_days INT,
            ADD COLUMN strategy_schema               JSONB
    """))
    op.execute(text(
        "CREATE INDEX bots_shadow_of_idx ON bots (shadow_of) WHERE shadow_of IS NOT NULL"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS bot_runs_bot_id_started_at_idx "
        "ON bot_runs (bot_id, started_at DESC)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS bot_orders_bot_id_placed_at_idx "
        "ON bot_orders (bot_id, placed_at DESC)"
    ))
    # Widen risk_decisions.attempt_kind CHECK to include shadow_place_order
    op.execute(text(
        "ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check"
    ))
    op.execute(text("""
        ALTER TABLE risk_decisions
            ADD CONSTRAINT risk_decisions_attempt_kind_check
                CHECK (attempt_kind IN (
                    'preview', 'place_order', 'modify_order',
                    'bot_place_order', 'shadow_place_order'
                ))
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE risk_decisions DROP CONSTRAINT risk_decisions_attempt_kind_check
    """))
    op.execute(text("""
        ALTER TABLE risk_decisions
            ADD CONSTRAINT risk_decisions_attempt_kind_check
                CHECK (attempt_kind IN (
                    'preview', 'place_order', 'modify_order', 'bot_place_order'
                ))
    """))
    op.execute(text("DROP INDEX IF EXISTS bot_orders_bot_id_placed_at_idx"))
    op.execute(text("DROP INDEX IF EXISTS bot_runs_bot_id_started_at_idx"))
    op.execute(text("DROP INDEX IF EXISTS bots_shadow_of_idx"))
    op.execute(text("""
        ALTER TABLE bots
            DROP COLUMN IF EXISTS strategy_schema,
            DROP COLUMN IF EXISTS shadow_comparison_window_days,
            DROP COLUMN IF EXISTS shadow_promoted_at,
            DROP COLUMN IF EXISTS shadow_of,
            DROP COLUMN IF EXISTS is_shadow
    """))
    op.execute(text("DROP TABLE IF EXISTS bot_param_suggestions"))
    op.execute(text("DROP FUNCTION IF EXISTS set_bot_param_suggestions_updated_at()"))
