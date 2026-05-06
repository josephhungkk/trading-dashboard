"""Phase 8b -- narrow active OCO links status index."""

from __future__ import annotations

from alembic import op

revision = "0017a_oco_links_partial_index"
down_revision = "0017_oco_capability_flip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_oco_links_status")
    op.execute(
        """
        CREATE INDEX idx_oco_links_status ON oco_links(status)
        WHERE status NOT IN ('COMPLETED', 'CANCELED', 'ERROR', 'CANCEL_FAILED')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_oco_links_status")
    op.execute(
        """
        CREATE INDEX idx_oco_links_status ON oco_links(status)
        WHERE status NOT IN ('COMPLETED', 'CANCELED')
        """
    )
