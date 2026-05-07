"""phase9: instrument_id resolver columns + best-effort backfill

Revision ID: 0023a
Revises: 0023
Create Date: 2026-05-07

Adds nullable instrument_id BIGINT FK columns to positions and
watchlist_entries with partial indexes, then performs a best-effort
backfill via instruments.canonical_id and symbol_aliases.

Rows that don't resolve (broker-specific symbols not yet in
symbol_aliases) stay NULL and are handled by the existing 7b.1.5
lazy resolver.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023a"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "watchlist_entries",
        sa.Column(
            "instrument_id",
            sa.BigInteger(),
            sa.ForeignKey("instruments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        "CREATE INDEX positions_instrument_idx"
        " ON positions(instrument_id)"
        " WHERE instrument_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX watchlist_entries_instrument_idx"
        " ON watchlist_entries(instrument_id)"
        " WHERE instrument_id IS NOT NULL"
    )
    # Best-effort backfill — positions matched via canonical_id
    op.execute(
        """
        UPDATE positions p
           SET instrument_id = i.id
          FROM instruments i
         WHERE i.canonical_id = p.canonical_id
           AND p.canonical_id IS NOT NULL
        """
    )
    # Best-effort backfill — watchlist_entries via symbol_aliases
    op.execute(
        """
        UPDATE watchlist_entries w
           SET instrument_id = sa.instrument_id
          FROM symbol_aliases sa
         WHERE sa.source     = w.broker_id
           AND sa.raw_symbol = w.symbol
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS watchlist_entries_instrument_idx")
    op.execute("DROP INDEX IF EXISTS positions_instrument_idx")
    op.drop_column("watchlist_entries", "instrument_id")
    op.drop_column("positions", "instrument_id")
