"""phase10b.1: bars_1d continuous aggregate of bars_1m

Revision ID: 0038_phase10b1_bars_1d
Revises: 0037b_risk_decisions_idx
Create Date: 2026-05-12

Adds ``bars_1d`` as a TimescaleDB continuous aggregate over ``bars_1m``
to feed the Phase 10b.1 volatility service (14-bar realized-vol + ATR for
vol-targeted position sizing).

Pattern: time-bucketed to UTC days (00:00–23:59), OHLC composed via
``first(open, bucket_start)`` / ``max(high)`` / ``min(low)`` /
``last(close, bucket_start)``. Volume aggregated when present.

Refresh policy:
  - Refresh window: 3 days behind / 1h ahead (re-covers late ticks)
  - Schedule: every 1 hour
  - Initial backfill is **synchronous** via ``refresh_continuous_aggregate``
    with NULL start (covers full bars_1m retention). Without this the CAGG
    sits empty until the first scheduled policy fire, breaking vol-targeted
    sizing for up to an hour after deploy (Chunk A+B database-reviewer HIGH).

Retention: 2 years on the aggregate; bars_1m only keeps 6 months, so once
1m chunks are retention-purged the daily aggregate becomes the long-history
source. This matches the Phase 9 design comment in 0024 ("CAGGs in 0025
carry the long history" — finally landed here, just for 1d instead of the
originally-planned multi-tier).

Downgrade drops the policy + the aggregate. Reversal of 0024 still requires
this migration to be reversed first.
"""

from __future__ import annotations

from alembic import op

revision = "0038_phase10b1_bars_1d"
down_revision = "0037b_risk_decisions_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Continuous aggregate must be created OUTSIDE a transaction in some
    # TimescaleDB versions; Alembic's per-migration BEGIN/COMMIT handles
    # this fine for CREATE MATERIALIZED VIEW since TS 2.7+. If the deploy
    # environment is on an older TS, the operator must run this manually.
    op.execute(
        """
        CREATE MATERIALIZED VIEW bars_1d
        WITH (timescaledb.continuous) AS
        SELECT
            instrument_id,
            time_bucket(INTERVAL '1 day', bucket_start) AS bar_date,
            first(open, bucket_start)  AS open,
            max(high)                  AS high,
            min(low)                   AS low,
            last(close, bucket_start)  AS close,
            sum(volume)                AS volume,
            sum(trade_count)           AS trade_count
        FROM bars_1m
        GROUP BY instrument_id, bar_date
        WITH NO DATA
        """
    )

    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
            'bars_1d',
            start_offset      => INTERVAL '3 days',
            end_offset        => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour'
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS bars_1d_inst_date_idx
            ON bars_1d (instrument_id, bar_date DESC)
        """
    )

    # Synchronous backfill of the full bars_1m history. NULL start means
    # "use the hypertable's earliest data". Blocks during migration so
    # vol-targeted sizing is immediately usable after deploy. Tens of
    # seconds for a fresh system; only what's already in bars_1m anyway.
    op.execute("CALL refresh_continuous_aggregate('bars_1d', NULL, NULL)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS bars_1d_inst_date_idx")
    op.execute(
        "SELECT remove_continuous_aggregate_policy('bars_1d', if_exists => true)"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS bars_1d CASCADE")
