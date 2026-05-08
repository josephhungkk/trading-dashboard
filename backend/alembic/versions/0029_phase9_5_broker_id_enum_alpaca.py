"""Phase 9.5 retro: extend broker_id_enum to include 'alpaca'.

Resolves Phase 7c CRIT-db-1: broker_id_enum was created in 0002 with only
('ibkr', 'futu', 'schwab'). Phase 7c added Alpaca but no ALTER TYPE migration
ran. Discoverer's CAST(:broker_id AS broker_id_enum) silently rejected every
Alpaca account INSERT.

Revision ID: 0029_phase9_5_broker_id_enum_alpaca
Revises: 0028b_oco_links_fk_explicit
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0029_phase9_5_broker_id_enum_alpaca"
down_revision = "0028b_oco_links_fk_explicit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE is non-transactional in PostgreSQL; emit COMMIT to
    # break out of Alembic's implicit transaction wrapper, then re-open a
    # transaction so the rest of the migration (if any) runs consistently.
    op.execute("COMMIT")
    op.execute("ALTER TYPE broker_id_enum ADD VALUE IF NOT EXISTS 'alpaca'")
    op.execute("BEGIN")


def downgrade() -> None:
    # PostgreSQL cannot DROP enum values that are in use (or at all pre-PG15).
    # Downgrade is intentionally a no-op; removing 'alpaca' would require
    # recreating the enum and every column that references it.
    pass
