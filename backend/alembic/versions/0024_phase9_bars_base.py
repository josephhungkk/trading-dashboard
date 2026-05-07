"""phase9: bars_1s + bars_1m hypertables with retention policies

Revision ID: 0024
Revises: 0023b
Create Date: 2026-05-07

Creates two TimescaleDB hypertables for OHLCV bar storage:
  - bars_1s: 1-second bars from the bar_aggregator (quote bus → redis)
  - bars_1m: 1-minute bars from sidecar GetHistoricalBars + aggregator emitter

Architecture invariants (architect findings applied inline):
  - PK (instrument_id, bucket_start) — single-row, no source in PK (HIGH #2)
  - volume NUMERIC(20,8) NULL with volume_source discriminator (HIGH #1)
  - bars_1m source_priority CHECK (1,2,3,4,99) (MED #3)
  - No inserted_at column — unused for tie-breaking, saves ~500MB/yr (MED #5)
  - FK ON DELETE CASCADE to keep hypertable consistent with instruments table

Downgrade drops both hypertables (CASCADE removes chunks; 0025 CAGGs must be
dropped first by their own downgrade before this migration is reversed).
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # bars_1s — populated by bar_aggregator from quote bus
    # chunk_time_interval = 6 hours (high write rate: ~1 row/s/instrument)
    # retention = 7 days (rolling window; CAGGs in 0025 carry the long history)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE bars_1s (
          instrument_id    BIGINT        NOT NULL
                           REFERENCES instruments(id) ON DELETE CASCADE,
          bucket_start     TIMESTAMPTZ   NOT NULL,
          source           TEXT          NOT NULL,
          source_priority  SMALLINT      NOT NULL DEFAULT 99,
          open             NUMERIC(20,8) NOT NULL,
          high             NUMERIC(20,8) NOT NULL,
          low              NUMERIC(20,8) NOT NULL,
          close            NUMERIC(20,8) NOT NULL,
          volume           NUMERIC(20,8),
          volume_source    TEXT          NOT NULL,
          trade_count      INTEGER       NOT NULL DEFAULT 0,
          PRIMARY KEY (instrument_id, bucket_start),
          CONSTRAINT bars_1s_volume_source_chk
            CHECK (volume_source IN ('tape', 'quote_proxy', 'none')),
          CONSTRAINT bars_1s_volume_consistent_chk CHECK (
            (volume_source = 'none' AND volume IS NULL) OR
            (volume_source <> 'none' AND volume IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
          'bars_1s',
          'bucket_start',
          chunk_time_interval => INTERVAL '6 hours'
        )
        """
    )
    op.execute(
        "CREATE INDEX bars_1s_inst_time_idx"
        " ON bars_1s (instrument_id, bucket_start DESC)"
    )
    op.execute("SELECT add_retention_policy('bars_1s', INTERVAL '7 days')")

    # -------------------------------------------------------------------------
    # bars_1m — populated by sidecar GetHistoricalBars + aggregator minute-emitter
    # chunk_time_interval = 7 days (lower write rate, moderate query range)
    # retention = 6 months (CAGGs in 0025 carry longer aggregated history)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE bars_1m (
          instrument_id    BIGINT        NOT NULL
                           REFERENCES instruments(id) ON DELETE CASCADE,
          bucket_start     TIMESTAMPTZ   NOT NULL,
          source           TEXT          NOT NULL,
          source_priority  SMALLINT      NOT NULL,
          open             NUMERIC(20,8) NOT NULL,
          high             NUMERIC(20,8) NOT NULL,
          low              NUMERIC(20,8) NOT NULL,
          close            NUMERIC(20,8) NOT NULL,
          volume           NUMERIC(20,8),
          volume_source    TEXT          NOT NULL,
          trade_count      INTEGER       NOT NULL DEFAULT 0,
          PRIMARY KEY (instrument_id, bucket_start),
          CONSTRAINT bars_1m_volume_source_chk
            CHECK (volume_source IN ('tape', 'quote_proxy', 'none')),
          CONSTRAINT bars_1m_volume_consistent_chk CHECK (
            (volume_source = 'none' AND volume IS NULL) OR
            (volume_source <> 'none' AND volume IS NOT NULL)
          ),
          CONSTRAINT bars_1m_priority_chk
            CHECK (source_priority IN (1, 2, 3, 4, 99))
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
          'bars_1m',
          'bucket_start',
          chunk_time_interval => INTERVAL '7 days'
        )
        """
    )
    op.execute(
        "CREATE INDEX bars_1m_inst_time_idx"
        " ON bars_1m (instrument_id, bucket_start DESC)"
    )
    op.execute("SELECT add_retention_policy('bars_1m', INTERVAL '6 months')")


def downgrade() -> None:
    # DROP TABLE cascades TimescaleDB chunks.  Run 0025 downgrade first to
    # remove any CAGGs that reference these hypertables before reversing here.
    op.execute("DROP TABLE IF EXISTS bars_1m")
    op.execute("DROP TABLE IF EXISTS bars_1s")
