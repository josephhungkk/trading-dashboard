"""Phase 22a fixup — correlation account_id NOT NULL + exposure partial index

Revision ID: 0070
Down Revision: 0069
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix MED-1: portfolio_correlation_snapshots.account_id must be NOT NULL
    op.execute(text(
        "ALTER TABLE portfolio_correlation_snapshots"
        " ALTER COLUMN account_id SET NOT NULL"
    ))

    # Fix MED-4: uq_portfolio_exposure_total partial index should guard on
    # instrument_id IS NULL (one total-notional limit per account), not on
    # limit_type = 'total_notional' which allows duplicates when no instrument_id.
    op.execute(text("DROP INDEX IF EXISTS uq_portfolio_exposure_total"))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_total"
        " ON portfolio_exposure_limits (account_id)"
        " WHERE instrument_id IS NULL"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_portfolio_exposure_total"))
    op.execute(text(
        "CREATE UNIQUE INDEX uq_portfolio_exposure_total"
        " ON portfolio_exposure_limits (account_id)"
        " WHERE limit_type = 'total_notional'"
    ))
    op.execute(text(
        "ALTER TABLE portfolio_correlation_snapshots"
        " ALTER COLUMN account_id DROP NOT NULL"
    ))
