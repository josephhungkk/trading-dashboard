"""Phase 22a — portfolio exposure limits + correlation snapshots + shadow promoted_via

Revision ID: 0069
Down Revision: 0068
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # portfolio_exposure_limits
    op.execute(text("""
        CREATE TABLE portfolio_exposure_limits (
            id              BIGSERIAL PRIMARY KEY,
            account_id      UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            limit_type      TEXT NOT NULL
                CHECK (limit_type IN ('total_notional','per_instrument')),
            instrument_id   BIGINT REFERENCES instruments(id) ON DELETE CASCADE,
            max_notional    NUMERIC(20,8) NOT NULL,
            currency        TEXT NOT NULL DEFAULT 'USD',
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_total"
        " ON portfolio_exposure_limits(account_id)"
        " WHERE limit_type = 'total_notional'"
    ))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_instr"
        " ON portfolio_exposure_limits(account_id, instrument_id)"
        " WHERE limit_type = 'per_instrument'"
    ))

    # portfolio_correlation_snapshots
    op.execute(text("""
        CREATE TABLE portfolio_correlation_snapshots (
            id              BIGSERIAL PRIMARY KEY,
            account_id      UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            instrument_ids  BIGINT[] NOT NULL,
            matrix_json     JSONB NOT NULL,
            window_days     INT NOT NULL DEFAULT 30
        )
    """))
    op.execute(text(
        "CREATE INDEX portfolio_correlation_snapshots_account_computed_idx"
        " ON portfolio_correlation_snapshots (account_id, computed_at DESC)"
    ))

    # shadow_promotion_events: add status column first (required for partial index)
    op.execute(text(
        "ALTER TABLE shadow_promotion_events"
        " ADD COLUMN status TEXT NOT NULL DEFAULT 'success'"
        " CHECK (status IN ('success','reverted'))"
    ))
    # shadow_promotion_events: add promoted_via + idempotency index
    op.execute(text(
        "ALTER TABLE shadow_promotion_events"
        " ADD COLUMN promoted_via TEXT CHECK (promoted_via IN ('manual','auto'))"
    ))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_shadow_promotion_success"
        " ON shadow_promotion_events(live_bot_id, shadow_bot_id)"
        " WHERE status = 'success'"
    ))

    # bots: auto_promote_criteria + last_auto_promote_check_at
    op.execute(text("""
        ALTER TABLE bots
            ADD COLUMN auto_promote_criteria JSONB
                CHECK (auto_promote_criteria IS NULL
                    OR (auto_promote_criteria ? 'min_sharpe'
                    AND auto_promote_criteria ? 'max_drawdown'
                    AND auto_promote_criteria ? 'min_win_rate')),
            ADD COLUMN last_auto_promote_check_at TIMESTAMPTZ
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS last_auto_promote_check_at"))
    op.execute(text("ALTER TABLE bots DROP COLUMN IF EXISTS auto_promote_criteria"))
    op.execute(text("DROP INDEX IF EXISTS uq_shadow_promotion_success"))
    op.execute(text(
        "ALTER TABLE shadow_promotion_events DROP COLUMN IF EXISTS promoted_via"
    ))
    op.execute(text(
        "ALTER TABLE shadow_promotion_events DROP COLUMN IF EXISTS status"
    ))
    op.execute(text("DROP TABLE IF EXISTS portfolio_correlation_snapshots"))
    op.execute(text("DROP TABLE IF EXISTS portfolio_exposure_limits"))
