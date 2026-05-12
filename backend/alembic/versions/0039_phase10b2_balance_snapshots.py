"""Phase 10b.2 §4.1 — account_balance_snapshots hypertable.

Append-only NLV history per broker_account. Writer hook in
brokers.py:1449 inserts on every NLV refresh; CAGGs in 0040
build 1h + 1d rollups on top.

Architecture invariants (architect review applied inline):
  - NO nlv >= 0 CHECK (CRIT #1: margin-call accounts have legit -ve NLV;
    broker_accounts.last_nlv has no such check per alembic 0003)
  - source_label CHECK (MED #1: dictionary-encoded compression defeated
    by unbounded text; constrain to lowercase-alnum-hyphen, <= 64 chars)
  - PK (account_id, ts); ts ordering index DESC
  - Hypertable chunk_time_interval = 7 days
  - Retention 2 years

Revision ID: 0039_phase10b2_snapshots
Down Revision: 0038_phase10b1_bars_1d
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "0039_phase10b2_snapshots"
down_revision = "0038_phase10b1_bars_1d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account_balance_snapshots (
          account_id    UUID          NOT NULL
                        REFERENCES broker_accounts(id) ON DELETE CASCADE,
          ts            TIMESTAMPTZ   NOT NULL,
          nlv           NUMERIC(20,8) NOT NULL,
          currency      CHAR(3)       NOT NULL,
          source_label  TEXT          NOT NULL,
          PRIMARY KEY (account_id, ts),
          CONSTRAINT ck_abs_currency_iso3 CHECK (currency ~ '^[A-Z]{3}$'),
          CONSTRAINT ck_abs_source_label  CHECK (
            source_label ~ '^[a-z0-9-]+$' AND length(source_label) <= 64
          )
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
          'account_balance_snapshots', 'ts',
          chunk_time_interval => INTERVAL '7 days'
        )
        """
    )
    op.execute(
        "CREATE INDEX abs_account_ts_idx"
        " ON account_balance_snapshots (account_id, ts DESC)"
    )
    op.execute(
        "SELECT add_retention_policy('account_balance_snapshots', INTERVAL '2 years')"
    )


def downgrade() -> None:
    # 0040 CAGGs must downgrade first to release dependencies.
    op.execute("DROP TABLE IF EXISTS account_balance_snapshots CASCADE")
