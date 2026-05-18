"""Phase 15a: FOREX asset class, forex_rfq_quotes table."""

from __future__ import annotations

from alembic import op

revision = "0051_phase15a_forex"
down_revision = "0050_phase14_futures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'FOREX'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'forex_max_notional_per_trade'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS forex_rfq_quotes (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id        UUID NOT NULL DEFAULT gen_random_uuid(),
            account_id        UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
            instrument_id     BIGINT NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
            bid               NUMERIC(20,8) NOT NULL,
            ask               NUMERIC(20,8) NOT NULL,
            ttl_seconds       INT NOT NULL,
            broker_quote_id   TEXT,
            side              TEXT CHECK (side IN ('BUY', 'SELL')),
            notional          NUMERIC(20,8),
            notional_currency TEXT,
            status            TEXT NOT NULL CHECK (status IN ('pending','accepting','accepted','expired','rejected')),
            reject_reason     TEXT,
            order_id          UUID REFERENCES orders(id) ON DELETE SET NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at        TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS forex_rfq_quotes_broker_quote_id_idx "
        "ON forex_rfq_quotes (broker_quote_id) WHERE broker_quote_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS forex_rfq_quotes_account_status_idx "
        "ON forex_rfq_quotes (account_id, status, expires_at)"
    )
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'forex_max_notional_per_trade', 100000, true, 'migration-0051')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forex_rfq_quotes")
    op.execute(
        "DELETE FROM risk_limits "
        "WHERE scope_type = 'global' AND limit_kind = 'forex_max_notional_per_trade'"
    )
