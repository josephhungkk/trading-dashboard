"""phase9: chart_layouts (single-tenant, 64KB cap)

Revision ID: 0026
Revises: 0024
Create Date: 2026-05-07

Single-tenant FE chart layout persistence (per-instrument). Multi-tenant deferred
post-v1.0 (CLAUDE.md non-goals); CF Access + Google IdP gates the perimeter.

  - UNIQUE on instrument_id (one layout per chart route).
  - Hard 64KB CHECK on payload size (architect MED #8) — at 70 indicators ×
    ~200 bytes config + 200 drawings × ~150 bytes = ~44KB worst-case under
    realistic use, well below the cap.
  - schema_version evolves the JSONB shape via one-shot Alembic data migrations
    + a read-side translator (architect HIGH #8). Reads NEVER mutate the row.
  - updated_at doubles as the recency signal for the active-set definition
    (BarService.active_set query).

Note: 0025 (CAGGs) is intentionally skipped here; it lands in Chunk B-bis after
Chunk B aggregator validates bars_1s shape (per spec §11 line 1061).
"""

from __future__ import annotations

from alembic import op


revision = "0026"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS — `chart_layouts` was hand-created on the NUC PG before
    # this migration existed (per integration/conftest.py note). The schema
    # is identical, so skipping creation is safe; this lets prod recover
    # without manual `alembic stamp` while dev/CI still create the table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chart_layouts (
          id              BIGSERIAL     PRIMARY KEY,
          instrument_id   BIGINT        NOT NULL
                          REFERENCES instruments(id) ON DELETE CASCADE,
          payload         JSONB         NOT NULL,
          schema_version  INTEGER       NOT NULL DEFAULT 1,
          updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
          UNIQUE (instrument_id),
          CONSTRAINT chart_layouts_payload_size_chk
            CHECK (octet_length(payload::text) < 65536)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS chart_layouts_updated_at_idx"
        " ON chart_layouts (updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chart_layouts")
