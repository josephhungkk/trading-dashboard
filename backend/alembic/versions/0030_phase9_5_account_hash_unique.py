"""Phase 9.5 retro: UNIQUE partial constraint on Schwab account_hash.

Per Phase 7a HIGH-db-1 fix (account_hash now populated by discoverer),
add UNIQUE constraint to catch duplicate-hash bugs at insertion time.
Replaces the non-unique idx_broker_accounts_schwab_hash from migration 0008.

Revision ID: 0030_phase9_5_account_hash_unique
Revises: 0029a_phase9_5_watchlist_unique
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0030_phase9_5_account_hash_unique"
down_revision = "0029a_phase9_5_watchlist_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_broker_accounts_schwab_hash")
    op.execute("""
        CREATE UNIQUE INDEX uq_broker_accounts_schwab_hash
          ON broker_accounts(broker_id, account_hash)
          WHERE account_hash IS NOT NULL AND broker_id = 'schwab'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_broker_accounts_schwab_hash")
    op.execute("""
        CREATE INDEX idx_broker_accounts_schwab_hash
          ON broker_accounts(broker_id, account_hash)
          WHERE account_hash IS NOT NULL
    """)
