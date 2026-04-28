"""advanced orders: brackets + fills + pending_fills + status-rank fn

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-28

Adds the rest of Phase 5c schema (the 'modified' enum value itself
shipped in 0006 to satisfy Postgres's "new enum value must be committed
before use" constraint):

- order_status_rank() function (consumer's _update_order rejects rank-decreasing)
- orders.parent_order_id + oca_group (bracket linkage)
- fills (execution-level audit trail with exec_id UNIQUE for resync idempotency)
- pending_fills (CRIT-2: buffer when execDetails arrives before order row)
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE orders
          ADD COLUMN parent_order_id UUID NULL REFERENCES orders(id) ON DELETE SET NULL,
          ADD COLUMN oca_group VARCHAR(64) NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX orders_parent_order_id_idx
          ON orders(parent_order_id)
          WHERE parent_order_id IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE FUNCTION order_status_rank(s order_status_enum) RETURNS INT AS $$
          SELECT CASE s
            WHEN 'pending_submit' THEN 0
            WHEN 'submitted'      THEN 1
            WHEN 'inactive'       THEN 1
            WHEN 'modified'       THEN 2
            WHEN 'partial'        THEN 3
            WHEN 'filled'         THEN 4
            WHEN 'cancelled'      THEN 5
            WHEN 'rejected'       THEN 5
            WHEN 'expired'        THEN 5
          END;
        $$ LANGUAGE SQL IMMUTABLE;
        """
    )
    op.execute(
        """
        CREATE TABLE fills (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
          exec_id             VARCHAR(64) NOT NULL UNIQUE,
          qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
          price               NUMERIC(20,8) NOT NULL,
          currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          executed_at         TIMESTAMPTZ NOT NULL,
          commission          NUMERIC(20,8) NULL,
          commission_currency CHAR(3) NULL CHECK (commission_currency IS NULL OR commission_currency ~ '^[A-Z]{3}$'),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX fills_order_id_executed_at_idx ON fills(order_id, executed_at DESC);"
    )
    op.execute("CREATE INDEX fills_executed_at_idx ON fills(executed_at);")
    op.execute(
        """
        CREATE TABLE pending_fills (
          exec_id             VARCHAR(64) PRIMARY KEY,
          broker_order_id     VARCHAR(64) NOT NULL,
          account_id          UUID NOT NULL REFERENCES broker_accounts(id),
          qty                 NUMERIC(20,8) NOT NULL CHECK (qty > 0),
          price               NUMERIC(20,8) NOT NULL,
          currency            CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          executed_at         TIMESTAMPTZ NOT NULL,
          commission          NUMERIC(20,8) NULL,
          commission_currency CHAR(3) NULL,
          raw_payload         JSONB NOT NULL,
          inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX pending_fills_broker_order_id_idx ON pending_fills(broker_order_id);"
    )
    op.execute("CREATE INDEX pending_fills_inserted_at_idx ON pending_fills(inserted_at);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_fills;")
    op.execute("DROP TABLE IF EXISTS fills;")
    op.execute("DROP FUNCTION IF EXISTS order_status_rank(order_status_enum);")
    op.execute("DROP INDEX IF EXISTS orders_parent_order_id_idx;")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS oca_group;")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS parent_order_id;")
