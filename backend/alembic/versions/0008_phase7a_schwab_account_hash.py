"""Phase 7a — Schwab account_hash privacy layer.

Adds:
  - broker_accounts.account_hash TEXT NULL
  - partial index on (broker_id, account_hash) WHERE account_hash IS NOT NULL

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-30
"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_accounts",
        sa.Column("account_hash", sa.Text(), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN broker_accounts.account_hash IS "
        "'Schwab-only: opaque account hash from /accountNumbers; required on "
        "all Schwab REST paths. NULL for non-Schwab brokers. Treated as "
        "PII-equivalent — never logged, boundary-stripped from REST responses.'"
    )
    op.execute(
        "CREATE INDEX idx_broker_accounts_schwab_hash "
        "ON broker_accounts(broker_id, account_hash) "
        "WHERE account_hash IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_broker_accounts_schwab_hash")
    op.drop_column("broker_accounts", "account_hash")
