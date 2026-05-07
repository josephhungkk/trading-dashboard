"""phase9: tick_size column on instruments

Revision ID: 0023b
Revises: 0023a
Create Date: 2026-05-07

Adds tick_size NUMERIC(20,8) NULL to instruments.  Nullable until first
observation from a broker contract-detail response.  The chart drag-handle
reads this to snap SL/TP prices to valid increments (e.g. BTC at Alpaca
= $0.01; HK equities tier from HK$0.001/0.01/0.05; penny stocks $0.0001).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023b"
down_revision = "0023a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("tick_size", sa.Numeric(20, 8), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN instruments.tick_size IS"
        " 'Minimum price increment. NULL until first observation from broker contract spec.'"
    )


def downgrade() -> None:
    op.drop_column("instruments", "tick_size")
