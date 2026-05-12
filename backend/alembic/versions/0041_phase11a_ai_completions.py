"""Phase 11a-A1 §6: cost ledger hypertable.

Chunk 7d, retention 1y, compress after 90d per LOW-5. Captures every
AI call attempt including failures so capacity planning is honest.
Writes are fire-and-forget batched in services/ai/cost_ledger.py
(HIGH-2 — fail-OPEN: ledger failures must never fail the AI call).

Revision ID: 0041_phase11a_ai_completions
Down Revision: 0040_phase10b2_caggs
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0041_phase11a_ai_completions"
down_revision = "0040_phase10b2_caggs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_completions (
            ts                TIMESTAMPTZ NOT NULL,
            request_id        UUID        NOT NULL,
            jwt_subject       TEXT        NOT NULL,
            capability        TEXT        NOT NULL,
            provider          TEXT        NOT NULL,
            model             TEXT        NOT NULL,
            host              TEXT        NOT NULL,
            prompt_tokens     INTEGER     NOT NULL DEFAULT 0,
            completion_tokens INTEGER     NOT NULL DEFAULT 0,
            wall_time_ms      INTEGER     NOT NULL DEFAULT 0,
            wol_warmup_ms     INTEGER     NOT NULL DEFAULT 0,
            outcome           TEXT        NOT NULL,
            error_class       TEXT,
            caller            TEXT        NOT NULL,
            CHECK (outcome IN ('ok', 'failed', 'timeout', 'rate_limited', 'fallback')),
            CHECK (capability ~ '^[A-Z_]+$'),
            CHECK (host IN ('nuc', 'heavy', 'cloud'))
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('ai_completions', 'ts', "
        "chunk_time_interval => INTERVAL '7 days');"
    )
    op.execute("SELECT add_retention_policy('ai_completions', INTERVAL '1 year');")
    op.execute(
        "ALTER TABLE ai_completions SET ("
        "  timescaledb.compress, "
        "  timescaledb.compress_segmentby = 'provider, capability'"
        ");"
    )
    op.execute("SELECT add_compression_policy('ai_completions', INTERVAL '90 days');")
    op.execute(
        "CREATE INDEX idx_ai_completions_subject_ts "
        "ON ai_completions (jwt_subject, ts DESC);"
    )
    op.execute(
        "CREATE INDEX idx_ai_completions_caller_ts "
        "ON ai_completions (caller, ts DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_completions CASCADE;")
