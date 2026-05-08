"""Phase 9.5 retro: partial index on broker_accounts.last_seen_via.

Revision ID: 0031a_phase9_5_last_seen_via_index
Revises: 0031_phase9_5_currency_base_check
"""
from __future__ import annotations

from alembic import op

revision = "0031a_phase9_5_last_seen_via_index"
down_revision = "0031_phase9_5_currency_base_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_broker_accounts_last_seen_via
          ON broker_accounts(last_seen_via)
          WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_broker_accounts_last_seen_via")
