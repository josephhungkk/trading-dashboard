"""Phase 9.5: order_events order FK SET NULL.

Revision ID: 0034_phase9_5_order_events_fk_set_null
Revises: 0033_phase9_5_fills_pending_indexes
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op

revision = "0034_phase9_5_order_events_fk_set_null"
down_revision = "0033_phase9_5_fills_pending_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE order_events
        DROP CONSTRAINT IF EXISTS order_events_order_id_fkey,
        ADD CONSTRAINT order_events_order_id_fkey
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE order_events
        DROP CONSTRAINT IF EXISTS order_events_order_id_fkey,
        ADD CONSTRAINT order_events_order_id_fkey
            FOREIGN KEY (order_id) REFERENCES orders(id)
        """
    )
