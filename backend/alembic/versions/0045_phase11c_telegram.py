"""Phase 11c-A: telegram command log and alert mute support.

Revision ID: 0045_phase11c_telegram
Down Revision: 0044_phase11b_alerts
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op

revision = "0045_phase11c_telegram"
down_revision = "0044_phase11b_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_command_log (
            id           BIGSERIAL,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
            chat_id      BIGINT NOT NULL,
            from_user_id BIGINT,
            command      TEXT NOT NULL,
            args         TEXT,
            outcome      TEXT NOT NULL CHECK (outcome IN ('ok','unauthorized','rate_limited','error','noop')),
            latency_ms   INT,
            PRIMARY KEY (ts, id)
        );
        """
    )
    op.execute(
        """
        SELECT create_hypertable('telegram_command_log','ts', if_not_exists => TRUE);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tg_cmd_chat_ts
          ON telegram_command_log (chat_id, ts DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tg_cmd_outcome_ts
          ON telegram_command_log (outcome, ts DESC) WHERE outcome != 'ok';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tg_cmd_id
          ON telegram_command_log (id DESC);
        """
    )
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS muted_until TIMESTAMPTZ;")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_muted_until
          ON alerts (muted_until) WHERE muted_until IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alerts_muted_until;")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS muted_until;")
    op.execute("DROP INDEX IF EXISTS idx_tg_cmd_id;")
    op.execute("DROP INDEX IF EXISTS idx_tg_cmd_outcome_ts;")
    op.execute("DROP INDEX IF EXISTS idx_tg_cmd_chat_ts;")
    op.execute("DROP TABLE IF EXISTS telegram_command_log CASCADE;")
