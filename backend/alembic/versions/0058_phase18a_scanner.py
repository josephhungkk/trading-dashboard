"""phase18a scanner tables

Revision ID: 0058
Revises: 0057
Create Date: 2026-05-19
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE saved_scans (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name              TEXT NOT NULL,
            universe_config   JSONB NOT NULL,
            rule_expr         TEXT NOT NULL,
            schedule          TEXT,
            market_hours_gate BOOLEAN NOT NULL DEFAULT false,
            exchange          TEXT,
            llm_depth         TEXT NOT NULL CHECK (llm_depth IN ('quick', 'deep')),
            alert_id          BIGINT REFERENCES alerts(id) ON DELETE SET NULL,
            enabled           BOOLEAN NOT NULL DEFAULT true,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE scanner_runs (
            id                UUID NOT NULL DEFAULT gen_random_uuid(),
            scan_id           UUID REFERENCES saved_scans(id) ON DELETE SET NULL,
            universe_snapshot JSONB NOT NULL,
            rule_expr         TEXT NOT NULL,
            candidate_count   INT NOT NULL DEFAULT 0,
            status            TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at      TIMESTAMPTZ,
            error             TEXT,
            PRIMARY KEY (id, started_at)
        )
    """)

    op.execute("""
        SELECT create_hypertable(
            'scanner_runs', 'started_at',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)

    op.execute("""
        SELECT add_retention_policy('scanner_runs', INTERVAL '90 days', if_not_exists => TRUE)
    """)

    op.execute("""
        CREATE TABLE scanner_candidates (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NOT NULL,
            instrument_id       BIGINT REFERENCES instruments(id) ON DELETE SET NULL,
            canonical_id        TEXT NOT NULL,
            matched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            indicator_snapshot  JSONB NOT NULL,
            llm_commentary      TEXT,
            llm_depth           TEXT CHECK (llm_depth IN ('quick', 'deep')),
            CHECK (instrument_id IS NOT NULL OR canonical_id IS NOT NULL)
        )
    """)

    op.execute("CREATE INDEX ON scanner_candidates (canonical_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scanner_candidates")
    op.execute("DROP TABLE IF EXISTS scanner_runs")
    op.execute("DROP TABLE IF EXISTS saved_scans")
