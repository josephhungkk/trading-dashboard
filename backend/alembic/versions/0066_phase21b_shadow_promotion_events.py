"""Phase 21b — shadow_promotion_events table

Revision ID: 0066
Down Revision: 0065
"""
from __future__ import annotations
from alembic import op
from sqlalchemy import text

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE shadow_promotion_events (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            shadow_bot_id            UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            live_bot_id              UUID NOT NULL REFERENCES bots(id) ON DELETE RESTRICT,
            promoted_by              TEXT NOT NULL,
            comparison_window_days   INT NOT NULL,
            comparison_window_start  TIMESTAMPTZ NOT NULL,
            shadow_metrics           JSONB NOT NULL,
            live_metrics             JSONB NOT NULL,
            promoted_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(text(
        "CREATE INDEX shadow_promotion_events_live_bot_id_idx "
        "ON shadow_promotion_events (live_bot_id, promoted_at DESC)"
    ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS shadow_promotion_events"))
