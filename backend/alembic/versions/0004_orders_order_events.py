"""orders_order_events

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-27
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
-- enums
CREATE TYPE order_side_enum AS ENUM ('BUY', 'SELL');
"""
    )
    op.execute(
        """
CREATE TYPE order_type_enum AS ENUM ('MARKET', 'LIMIT', 'STOP');
"""
    )
    op.execute(
        """
CREATE TYPE order_tif_enum AS ENUM ('DAY', 'GTC');
"""
    )
    op.execute(
        """
CREATE TYPE order_status_enum AS ENUM (
  'pending_submit',  -- accepted by backend, not yet acked by sidecar
  'submitted',       -- IBKR PendingSubmit / Submitted / PreSubmitted
  'partial',         -- filled_qty > 0 AND filled_qty < qty
  'filled',          -- terminal: filled_qty == qty
  'cancelled',       -- terminal: user-initiated cancel
  'rejected',        -- terminal: broker rejected
  'expired',         -- terminal: DAY order at session close
  'inactive'         -- broker marked Inactive
);
"""
    )
    op.execute(
        """
CREATE TABLE orders (
  id                UUID PRIMARY KEY,                    -- server-generated UUIDv7 (NOT frontend-controlled; R2 fix)
  account_id        UUID NOT NULL REFERENCES broker_accounts(id),
  client_order_id   UUID NOT NULL,                       -- frontend-generated UUID4; dedup key
  broker_order_id   TEXT,                                -- IBKR permId; nullable until ack
  conid             TEXT NOT NULL,
  symbol            TEXT NOT NULL,                       -- denormalized for UI
  side              order_side_enum NOT NULL,
  order_type        order_type_enum NOT NULL,
  tif               order_tif_enum NOT NULL,
  qty               NUMERIC(20, 8) NOT NULL,
  limit_price       NUMERIC(20, 8),                      -- NULL for MARKET
  stop_price        NUMERIC(20, 8),                      -- NULL unless STOP
  status            order_status_enum NOT NULL DEFAULT 'pending_submit',
  filled_qty        NUMERIC(20, 8) NOT NULL DEFAULT 0,
  avg_fill_price    NUMERIC(20, 8),
  notional          NUMERIC(20, 8) NOT NULL,             -- qty × price (limit) OR qty × mid × 1.05 (market; R12 slippage buffer)
  notional_filled   NUMERIC(20, 8) NOT NULL DEFAULT 0,   -- filled_qty × avg_fill_price; updated on every fill (R12)
  cancel_requested_at TIMESTAMPTZ,                       -- DELETE idempotency cooldown (R31)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_event_at     TIMESTAMPTZ,
  CHECK (
    (order_type = 'MARKET' AND limit_price IS NULL AND stop_price IS NULL) OR
    (order_type = 'LIMIT'  AND limit_price IS NOT NULL AND stop_price IS NULL) OR
    (order_type = 'STOP'   AND limit_price IS NULL AND stop_price IS NOT NULL)
  ),
  CHECK (filled_qty >= 0 AND filled_qty <= qty),
  CHECK (qty > 0)
);
"""
    )
    op.execute(
        """
-- Composite UNIQUE constraints (R2 + R19 fixes):
CREATE UNIQUE INDEX uq_orders_account_client_order_id ON orders (account_id, client_order_id);
"""
    )
    op.execute(
        """
CREATE UNIQUE INDEX uq_orders_account_broker_order_id ON orders (account_id, broker_order_id)
  WHERE broker_order_id IS NOT NULL;
"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_account_status ON orders (account_id, status)
  WHERE status IN (
    'pending_submit'::order_status_enum,
    'submitted'::order_status_enum,
    'partial'::order_status_enum
  );
"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_account_created ON orders (account_id, created_at DESC);
"""
    )
    op.execute(
        """
CREATE INDEX ix_orders_pending_submit_watchdog ON orders (created_at)
  WHERE status = 'pending_submit'::order_status_enum;   -- watchdog scan path (R1+R9 fix)
"""
    )
    op.execute(
        """
CREATE TABLE order_events (
  id                BIGSERIAL PRIMARY KEY,
  order_id          UUID REFERENCES orders(id),          -- nullable for TWS-placed orders
  account_id        UUID NOT NULL REFERENCES broker_accounts(id),
  broker_order_id   TEXT,
  status            order_status_enum NOT NULL,
  filled_qty        NUMERIC(20, 8),
  avg_fill_price    NUMERIC(20, 8),
  broker_event_at   TIMESTAMPTZ NOT NULL,
  observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload       JSONB
);
"""
    )
    op.execute(
        """
CREATE INDEX ix_order_events_order_id ON order_events (order_id, broker_event_at DESC);
"""
    )
    op.execute(
        """
CREATE INDEX ix_order_events_account ON order_events (account_id, broker_event_at DESC);
"""
    )


def downgrade() -> None:
    op.drop_index("ix_order_events_account", table_name="order_events")
    op.drop_index("ix_order_events_order_id", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_orders_pending_submit_watchdog", table_name="orders")
    op.drop_index("ix_orders_account_created", table_name="orders")
    op.drop_index("ix_orders_account_status", table_name="orders")
    op.drop_index("uq_orders_account_broker_order_id", table_name="orders")
    op.drop_index("uq_orders_account_client_order_id", table_name="orders")
    op.drop_table("orders")
    op.execute("DROP TYPE order_status_enum")
    op.execute("DROP TYPE order_tif_enum")
    op.execute("DROP TYPE order_type_enum")
    op.execute("DROP TYPE order_side_enum")
