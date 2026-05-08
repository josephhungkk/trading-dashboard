"""Phase 9.5 retro: enforce UNIQUE on watchlist_entries(broker_id, symbol).

Resolves Phase 7c HIGH-db-2: watchlist_entries had a plain (non-unique) index
on (broker_id, symbol), allowing duplicates to accumulate via bar_service
UNION ALL backfill. This migration deduplicates existing rows and replaces the
plain index with a UNIQUE index.

Revision ID: 0029a_phase9_5_watchlist_unique
Revises: 0029_phase9_5_broker_id_enum_alpaca
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0029a_phase9_5_watchlist_unique"
down_revision = "0029_phase9_5_broker_id_enum_alpaca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove duplicate rows, keeping the one with the lowest id (UUID ordering).
    op.execute("""
        DELETE FROM watchlist_entries a USING watchlist_entries b
         WHERE a.id > b.id
           AND a.broker_id = b.broker_id
           AND a.symbol = b.symbol
    """)
    op.execute("DROP INDEX IF EXISTS watchlist_entries_broker_symbol_idx")
    op.execute("""
        CREATE UNIQUE INDEX watchlist_entries_broker_symbol_uq
          ON watchlist_entries(broker_id, symbol)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS watchlist_entries_broker_symbol_uq")
    op.execute("""
        CREATE INDEX watchlist_entries_broker_symbol_idx
          ON watchlist_entries(broker_id, symbol)
    """)
