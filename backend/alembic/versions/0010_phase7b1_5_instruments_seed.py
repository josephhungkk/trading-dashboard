"""phase 7b.1.5: instruments seed support.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE positions
          ADD COLUMN symbol TEXT,
          ADD COLUMN primary_exchange TEXT,
          ADD COLUMN canonical_id TEXT;
        """
    )
    op.execute(
        """
        CREATE INDEX positions_canonical_id_idx
          ON positions(canonical_id)
         WHERE canonical_id IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE TABLE watchlist_entries (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          broker_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          exchange TEXT NOT NULL,
          currency CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX watchlist_entries_broker_symbol_idx
          ON watchlist_entries(broker_id, symbol);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS watchlist_entries_broker_symbol_idx;")
    op.execute("DROP TABLE IF EXISTS watchlist_entries;")
    op.execute("DROP INDEX IF EXISTS positions_canonical_id_idx;")
    op.execute(
        """
        ALTER TABLE positions
          DROP COLUMN IF EXISTS canonical_id,
          DROP COLUMN IF EXISTS primary_exchange,
          DROP COLUMN IF EXISTS symbol;
        """
    )
