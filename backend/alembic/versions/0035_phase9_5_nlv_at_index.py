"""Phase 9.5 retro: partial index on broker_accounts.last_nlv_at.

Phase 5a retro M1 — staleness queries on `last_nlv_at` (admin/analytics
filtering on rows that haven't refreshed in N minutes) currently seq-scan
broker_accounts. Tens of rows today; latent gap before scaling.

Revision ID: 0035_phase9_5_nlv_at_index
Revises: 0034a_phase9_5_positions_updated_at_trigger
"""

from alembic import op

revision = "0035_phase9_5_nlv_at_index"
down_revision = "0034a_phase9_5_positions_updated_at_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_broker_accounts_last_nlv_at
            ON broker_accounts(last_nlv_at)
            WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_broker_accounts_last_nlv_at")
