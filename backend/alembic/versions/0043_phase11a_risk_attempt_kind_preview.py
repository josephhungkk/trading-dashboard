"""Phase 11a CI-debt: extend risk_decisions.attempt_kind CHECK with 'preview'.

Phase 10a.5.1 C1 added preview WARN/BLOCK audit
(``orders_service.preview_order`` writes a RiskDecision with
``attempt_kind="preview"`` when the verdict isn't ``allow``), but the
alembic 0036 CHECK constraint still only allowed
``('place_order', 'modify_order')``.

Net effect since Phase 10a.5.1: every preview WARN/BLOCK silently raised
``CheckViolation`` inside the audit ``try/except Exception``, was
swallowed to ``log.exception("risk.audit_insert_failed")`` +
``risk_audit_insert_failures_total{attempt_kind="preview"}.inc()``, and
the preview itself proceeded as if audit succeeded.

This migration drops the old CHECK and recreates it with the widened
value set. Idempotent: ``IF EXISTS`` on the drop so a re-run after a
manual fix is safe.

Revision ID: 0043_phase11a_risk_attempt_kind_preview
Down Revision: 0042_phase11a_ai_jobs
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0043_phase11a_risk_attempt_kind_preview"
down_revision = "0042_phase11a_ai_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # asyncpg prepared statements forbid multiple DDL commands in one
    # execute(); issue each ALTER as its own call.
    op.execute(
        "ALTER TABLE risk_decisions "
        "DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check"
    )
    op.execute(
        "ALTER TABLE risk_decisions "
        "ADD CONSTRAINT risk_decisions_attempt_kind_check "
        "CHECK (attempt_kind IN ('preview', 'place_order', 'modify_order'))"
    )


def downgrade() -> None:
    # Best-effort: if any 'preview' rows exist they'd violate the old
    # CHECK on add. Delete them first — they're audit log entries, no
    # FK references, safe to drop. Operators concerned about audit
    # retention should export before downgrading.
    op.execute("DELETE FROM risk_decisions WHERE attempt_kind = 'preview'")
    op.execute(
        "ALTER TABLE risk_decisions "
        "DROP CONSTRAINT IF EXISTS risk_decisions_attempt_kind_check"
    )
    op.execute(
        "ALTER TABLE risk_decisions "
        "ADD CONSTRAINT risk_decisions_attempt_kind_check "
        "CHECK (attempt_kind IN ('place_order', 'modify_order'))"
    )
