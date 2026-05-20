"""Phase 22c — bot_health_snapshots hypertable

Revision ID: 0072
Down Revision: 0071
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0072"
down_revision = "0069_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            "CREATE TABLE IF NOT EXISTS bot_health_snapshots ("
            "    bot_id UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,"
            "    snapshot_at TIMESTAMPTZ NOT NULL,"
            "    sharpe_30d NUMERIC(10,4),"
            "    sharpe_7d NUMERIC(10,4),"
            "    max_drawdown NUMERIC(10,4),"
            "    win_rate NUMERIC(10,4),"
            "    total_pnl NUMERIC(20,8),"
            "    trade_count INT,"
            "    advisor_veto_accuracy_1h NUMERIC(10,4),"
            "    exposure_utilisation NUMERIC(10,4),"
            "    PRIMARY KEY (bot_id, snapshot_at)"
            ")"
        )
    )
    op.execute(text("SELECT create_hypertable('bot_health_snapshots', 'snapshot_at')"))
    op.execute(
        text(
            "CREATE INDEX bot_health_snapshots_bot_id_idx"
            " ON bot_health_snapshots (bot_id, snapshot_at DESC)"
        )
    )
    op.execute(
        text("SELECT add_retention_policy('bot_health_snapshots', INTERVAL '2 years')")
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS bot_health_snapshots CASCADE"))
