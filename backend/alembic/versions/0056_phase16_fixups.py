"""Phase 16 fixups: broker_accounts.country CHECK, missing FK indexes, fund_nav query index."""
from __future__ import annotations
from alembic import op


revision = "0056_phase16_fixups"
down_revision = "0055_phase16c_cfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CRIT: add 2-char ISO country code constraint (NULL allowed = jurisdiction unknown)
    op.execute(
        "ALTER TABLE broker_accounts"
        " ADD CONSTRAINT broker_accounts_country_iso2_check"
        " CHECK (country IS NULL OR (length(country) = 2 AND country = upper(country)))"
    )
    # HIGH: missing FK index on bonds_accrued_interest.account_id
    op.execute(
        "CREATE INDEX IF NOT EXISTS bonds_accrued_interest_account_idx"
        " ON bonds_accrued_interest (account_id)"
    )
    # HIGH: efficient per-instrument NAV lookup across TimescaleDB chunks
    op.execute(
        "CREATE INDEX IF NOT EXISTS fund_nav_snapshots_instrument_captured_idx"
        " ON fund_nav_snapshots (instrument_id, captured_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS fund_nav_snapshots_instrument_captured_idx")
    op.execute("DROP INDEX IF EXISTS bonds_accrued_interest_account_idx")
    op.execute(
        "ALTER TABLE broker_accounts DROP CONSTRAINT IF EXISTS broker_accounts_country_iso2_check"
    )
