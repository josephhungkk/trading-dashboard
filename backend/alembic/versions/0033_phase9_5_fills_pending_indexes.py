"""Phase 9.5 retro: add pending_fills and fills ordering indexes.

Revision ID: 0033_phase9_5_fills_pending_indexes
Revises: 0032_phase9_5_order_status_rank_modified
"""

from alembic import op

revision = "0033_phase9_5_fills_pending_indexes"
down_revision = "0032_phase9_5_order_status_rank_modified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS pending_fills_broker_order_id_account_id_idx
        ON pending_fills(broker_order_id, account_id);
        """
    )
    op.execute("DROP INDEX IF EXISTS fills_executed_at_idx;")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS fills_executed_at_id_desc_idx
        ON fills(executed_at DESC, id DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS fills_executed_at_id_desc_idx;")
    op.execute("CREATE INDEX IF NOT EXISTS fills_executed_at_idx ON fills(executed_at);")
    op.execute("DROP INDEX IF EXISTS pending_fills_broker_order_id_account_id_idx;")
