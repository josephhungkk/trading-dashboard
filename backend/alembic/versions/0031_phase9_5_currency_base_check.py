"""Phase 9.5 retro: add ISO-3 CHECK on broker_accounts.currency_base.

Revision ID: 0031_phase9_5_currency_base_check
Revises: 0030_phase9_5_account_hash_unique
"""
from __future__ import annotations

from alembic import op

revision = "0031_phase9_5_currency_base_check"
down_revision = "0030_phase9_5_account_hash_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE broker_accounts
          ADD CONSTRAINT broker_accounts_currency_base_iso3
          CHECK (currency_base = '' OR currency_base ~ '^[A-Z]{3}$')
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE broker_accounts
        DROP CONSTRAINT IF EXISTS broker_accounts_currency_base_iso3
    """)
