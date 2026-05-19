"""fix scanner_candidates — add run_started_at + FK to scanner_runs(id, started_at)

TimescaleDB hypertables require the partition column in any unique/FK target.
scanner_runs PK is (id, started_at). We add run_started_at to scanner_candidates
so we can place a proper FK.

Revision ID: 0058a
Revises: 0058
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op

revision = "0058a"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scanner_candidates "
        "ADD COLUMN IF NOT EXISTS run_started_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE scanner_candidates "
        "ADD CONSTRAINT scanner_candidates_run_fkey "
        "FOREIGN KEY (run_id, run_started_at) "
        "REFERENCES scanner_runs (id, started_at) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE scanner_candidates "
        "DROP CONSTRAINT IF EXISTS scanner_candidates_run_fkey"
    )
    op.execute(
        "ALTER TABLE scanner_candidates "
        "DROP COLUMN IF EXISTS run_started_at"
    )
