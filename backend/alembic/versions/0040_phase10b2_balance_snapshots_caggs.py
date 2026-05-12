"""Phase 10b.2 §4.2 — 1h + 1d CAGGs over account_balance_snapshots.

Architecture invariants (architect review applied inline):
  - autocommit_block() for refresh_continuous_aggregate (CRIT #3:
    PROCEDURE rejects running inside a TX; transaction_per_migration=True
    breaks the naive op.execute("CALL ...") pattern. Phase 10b.1 alembic
    0038 has the same bug; it only worked because bars_1m was empty.
    Retraction logged in spec §15.)
  - materialized_only = false (MED #3: real-time aggregation closes the
    gap between deploy and first scheduled refresh)
  - Explicit retention on both CAGGs (MED #2: every CAGG declares retention)

Revision ID: 0040_phase10b2_caggs
Down Revision: 0039_phase10b2_snapshots
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0040_phase10b2_caggs"
down_revision = "0039_phase10b2_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1h CAGG — feeds window=30d
    op.execute(
        """
        CREATE MATERIALIZED VIEW account_balance_snapshots_1h
        WITH (timescaledb.continuous) AS
        SELECT
          account_id,
          time_bucket(INTERVAL '1 hour', ts) AS bucket,
          last(nlv, ts)       AS nlv_close,
          last(currency, ts)  AS currency,
          MAX(nlv)            AS nlv_high,
          MIN(nlv)            AS nlv_low,
          first(nlv, ts)      AS nlv_open
        FROM account_balance_snapshots
        GROUP BY account_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
          'account_balance_snapshots_1h',
          start_offset => INTERVAL '7 days',
          end_offset   => INTERVAL '1 hour',
          schedule_interval => INTERVAL '30 minutes'
        )
        """
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots_1h', INTERVAL '1 year')"
    )
    op.execute(
        "ALTER MATERIALIZED VIEW account_balance_snapshots_1h"
        " SET (timescaledb.materialized_only = false)"
    )

    # 1d CAGG — feeds window=1y
    op.execute(
        """
        CREATE MATERIALIZED VIEW account_balance_snapshots_1d
        WITH (timescaledb.continuous) AS
        SELECT
          account_id,
          time_bucket(INTERVAL '1 day', ts) AS bucket,
          last(nlv, ts)       AS nlv_close,
          last(currency, ts)  AS currency,
          MAX(nlv)            AS nlv_high,
          MIN(nlv)            AS nlv_low,
          first(nlv, ts)      AS nlv_open
        FROM account_balance_snapshots
        GROUP BY account_id, bucket
        WITH NO DATA
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
          'account_balance_snapshots_1d',
          start_offset => INTERVAL '90 days',
          end_offset   => INTERVAL '1 day',
          schedule_interval => INTERVAL '6 hours'
        )
        """
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots_1d', INTERVAL '10 years')"
    )
    op.execute(
        "ALTER MATERIALIZED VIEW account_balance_snapshots_1d"
        " SET (timescaledb.materialized_only = false)"
    )

    # Synchronous initial backfill — autocommit_block escapes the migration TX
    # so refresh_continuous_aggregate's internal COMMITs are legal.
    with op.get_context().autocommit_block():
        op.execute(
            "CALL refresh_continuous_aggregate('account_balance_snapshots_1h', NULL, NULL)"
        )
        op.execute(
            "CALL refresh_continuous_aggregate('account_balance_snapshots_1d', NULL, NULL)"
        )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS account_balance_snapshots_1d CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS account_balance_snapshots_1h CASCADE")
