"""Phase 11a-A1 §6: ai_jobs async-job store.

Per-state-transition timestamps for split orphan-recovery thresholds
(HIGH-8): ``warming`` jobs cutoff = 90s; ``inferring`` jobs cutoff =
10min. The partial index on the three live states keeps the
orphan-recovery scan ``O(in_flight)`` not ``O(all_jobs)``.

LOW-6: plain table not hypertable — job volume is small + queries are
status-driven not time-range-driven.

Revision ID: 0042_phase11a_ai_jobs
Down Revision: 0041_phase11a_ai_completions
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0042_phase11a_ai_jobs"
down_revision = "0041_phase11a_ai_completions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_jobs (
            id                    UUID        PRIMARY KEY,
            jwt_subject           TEXT        NOT NULL,
            status                TEXT        NOT NULL,
            capability            TEXT        NOT NULL,
            request_jsonb         JSONB       NOT NULL,
            response_jsonb        JSONB,
            error                 TEXT,
            started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            warming_started_at    TIMESTAMPTZ,
            inferring_started_at  TIMESTAMPTZ,
            completed_at          TIMESTAMPTZ,
            cancel_requested      BOOLEAN     NOT NULL DEFAULT false,
            CHECK (status IN (
                'pending','warming','inferring','completed','failed','cancelled'
            ))
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_ai_jobs_status_started_at "
        "ON ai_jobs (status, started_at) "
        "WHERE status IN ('pending','warming','inferring');"
    )
    op.execute(
        "CREATE INDEX idx_ai_jobs_subject_started_at "
        "ON ai_jobs (jwt_subject, started_at DESC);"
    )


def downgrade() -> None:
    # database-reviewer M1: CASCADE forward-compatible with FKs that
    # later chunks (services/ai/jobs.py wiring) may reference this table.
    op.execute("DROP TABLE IF EXISTS ai_jobs CASCADE;")
