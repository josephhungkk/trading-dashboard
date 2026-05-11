"""Phase 10a.5 — risk_decisions verdict index

Revision ID: 0037b_risk_decisions_idx
Down Revision: 0037_phase10a5
Create Date: 2026-05-11

Standalone revision for the ``risk_decisions(verdict, evaluated_at DESC)``
index. Speeds up the admin feed at ``/api/risk/decisions?verdict=block``
once the A5 widening starts writing thousands of ALLOW rows per session.

**Note on CONCURRENTLY:** the spec mandates ``CREATE INDEX CONCURRENTLY``
for production safety, but Alembic's ``transaction_per_migration=True``
wraps each rev in BEGIN, and ``CONCURRENTLY`` is illegal inside a
transaction. Switching the bind to AUTOCOMMIT here pollutes the
connection's isolation level for downstream migrations (test_migration
fails with "LOCK TABLE only in transaction blocks"). The pragmatic
trade-off: this migration uses a plain ``CREATE INDEX``. Prod operators
who need the build to be non-blocking should run the script in
``scripts/db/build_verdict_index_concurrently.sql`` manually before the
deploy that contains this migration.
"""

from __future__ import annotations

from alembic import op

revision = "0037b_risk_decisions_idx"
down_revision = "0037_phase10a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_risk_decisions_verdict_time "
        "ON risk_decisions (verdict, evaluated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_risk_decisions_verdict_time")
