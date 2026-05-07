"""phase9: bar_backfill_jobs (idempotency + dedup)

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-07

Job-tracking table for the BarService cold-fetch + pre-warm flow:

  - One row per (instrument_id, source, timeframe, [range_start, range_end))
    backfill attempt.
  - Partial UNIQUE on the range tuple WHERE status IN ('pending','in_progress')
    is the cross-worker chokepoint (architect CRIT #4): two concurrent
    backend workers racing the same fetch can both INSERT, but only one will
    survive — the loser knows from `was_new=false` (xmax=0 trick) and waits
    via pg_notify('bar_backfill_done') / 250 ms poll fallback.
  - Status check constraint enforces lifecycle: pending → in_progress →
    done | failed.
"""

from __future__ import annotations

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bar_backfill_jobs (
          id              BIGSERIAL   PRIMARY KEY,
          instrument_id   BIGINT      NOT NULL
                          REFERENCES instruments(id) ON DELETE CASCADE,
          source          TEXT        NOT NULL,
          timeframe       TEXT        NOT NULL,
          range_start     TIMESTAMPTZ NOT NULL,
          range_end       TIMESTAMPTZ NOT NULL,
          status          TEXT        NOT NULL,
          rows_inserted   INTEGER,
          error_message   TEXT,
          started_at      TIMESTAMPTZ,
          finished_at     TIMESTAMPTZ,
          inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT bbj_status_chk
            CHECK (status IN ('pending','in_progress','done','failed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX bbj_inst_tf_status_idx"
        " ON bar_backfill_jobs (instrument_id, timeframe, status)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX bbj_unique_pending_idx
          ON bar_backfill_jobs
          (instrument_id, source, timeframe, range_start, range_end)
          WHERE status IN ('pending', 'in_progress')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bar_backfill_jobs")
