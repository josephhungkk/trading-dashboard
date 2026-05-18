"""Phase 15b: CRYPTO asset class, crypto_order_book_snapshots hypertable."""

from __future__ import annotations

from alembic import op

revision = "0052_phase15b_crypto"
down_revision = "0051_phase15a_forex"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'CRYPTO'")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS crypto_order_book_snapshots (
            instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            source        TEXT NOT NULL DEFAULT 'coinbase',
            level         INT NOT NULL,
            side          TEXT NOT NULL CHECK (side IN ('bid', 'ask')),
            price         NUMERIC(20, 8) NOT NULL,
            qty           NUMERIC(20, 8) NOT NULL,
            captured_at   TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('crypto_order_book_snapshots', 'captured_at')"
    )
    op.execute(
        "SELECT add_retention_policy('crypto_order_book_snapshots', INTERVAL '7 days')"
    )

    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE MATERIALIZED VIEW crypto_order_book_1h
            WITH (timescaledb.continuous) AS
            SELECT time_bucket(INTERVAL '1 hour', captured_at) AS bucket,
                   instrument_id, source, side, level,
                   first(price, captured_at) AS price_open,
                   last(price, captured_at)  AS price_close,
                   avg(qty)                  AS qty_avg
            FROM   crypto_order_book_snapshots
            WHERE  level <= 3
            GROUP BY bucket, instrument_id, source, side, level
            WITH NO DATA
            """
        )
        op.execute(
            "ALTER MATERIALIZED VIEW crypto_order_book_1h"
            " SET (timescaledb.materialized_only = false)"
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy(
                'crypto_order_book_1h',
                start_offset      => INTERVAL '7 days',
                end_offset        => INTERVAL '1 hour',
                schedule_interval => INTERVAL '1 hour'
            )
            """
        )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS crypto_order_book_1h CASCADE")
    op.execute("DROP TABLE IF EXISTS crypto_order_book_snapshots")
