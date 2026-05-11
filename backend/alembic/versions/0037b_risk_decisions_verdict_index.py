"""Phase 10a.5 — risk_decisions verdict index (CONCURRENTLY)

Revision ID: 0037b_risk_decisions_idx
Down Revision: 0037_phase10a5
Create Date: 2026-05-11

Standalone revision for ``CREATE INDEX CONCURRENTLY`` — cannot run inside
a transaction wrapper (env.py sets ``transaction_per_migration=True`` for
0037). Splitting into its own rev lets alembic apply this with autocommit
isolation via the ``transactional_ddl = False`` hint below.

The index speeds up the admin feed at ``/api/risk/decisions?verdict=block``
once the A5 widening starts writing thousands of ALLOW rows per session.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037b_risk_decisions_idx"
down_revision = "0037_phase10a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Switch to autocommit isolation so CONCURRENTLY is permitted.
    # env.py wraps the rev in a transaction; we exit it explicitly.
    if bind.in_transaction():
        bind.commit()
    bind = bind.execution_options(isolation_level="AUTOCOMMIT")
    bind.execute(
        sa.text(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_risk_decisions_verdict_time "
            "ON risk_decisions (verdict, evaluated_at DESC)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.in_transaction():
        bind.commit()
    bind = bind.execution_options(isolation_level="AUTOCOMMIT")
    bind.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS idx_risk_decisions_verdict_time"))
