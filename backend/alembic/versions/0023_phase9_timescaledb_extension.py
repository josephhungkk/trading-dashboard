"""phase9: install timescaledb extension

Revision ID: 0023
Revises: 0022_alpaca_oco_capability
Create Date: 2026-05-07

Idempotent — CREATE EXTENSION IF NOT EXISTS is safe to re-run.
Downgrade is a no-op: dropping timescaledb cascades to all hypertables
and CAGGs created by subsequent migrations; manual ops only.

PG-18 requires TimescaleDB ≥ 2.17.  Verify the installed version with:
    SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022_alpaca_oco_capability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    # Do not auto-DROP: cascades to hypertables and CAGGs built in 0024/0025.
    # Run manual DROP EXTENSION timescaledb CASCADE only after removing
    # all dependent objects created by subsequent migrations.
    pass
