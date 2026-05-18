"""Phase 14: FUTURE asset class, futures_roll_rules, futures_settlement_events."""

from __future__ import annotations

from alembic import op

revision = "0050_phase14_futures"
down_revision = "0049a_combo_orders_updated_at_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen the PG enum (must run outside a transaction)
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'FUTURE'")

    # 2. futures_roll_rules
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_roll_rules (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            days_before   SMALLINT NOT NULL CHECK (days_before BETWEEN 1 AND 90),
            enabled       BOOLEAN NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (account_id, instrument_id)
        )
        """
    )
    op.execute("DROP TRIGGER IF EXISTS futures_roll_rules_updated_at ON futures_roll_rules")
    op.execute(
        """
        CREATE TRIGGER futures_roll_rules_updated_at
        BEFORE UPDATE ON futures_roll_rules
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at()
        """
    )

    # 3. futures_settlement_events (append-only - no updated_at trigger)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_settlement_events (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id       UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
            instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
            settlement_price NUMERIC(20,8) NOT NULL,
            cash_delta       NUMERIC(20,8) NOT NULL,
            settlement_type  TEXT NOT NULL CHECK (settlement_type IN ('CASH','PHYSICAL')),
            broker_event_id  TEXT,
            settled_at       TIMESTAMPTZ NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS futures_settlement_events_account_settled_at "
        "ON futures_settlement_events (account_id, settled_at DESC)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS futures_settlement_events_dedup
        ON futures_settlement_events (account_id, broker_event_id)
        WHERE broker_event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_settlement_events")
    op.execute("DROP TABLE IF EXISTS futures_roll_rules")
