"""combo_orders, order_legs, orders.combo_id, risk_limits/risk_decisions widening

Revision ID: 0049
Revises: 0048_fix_guard_delete_trigger
"""

from __future__ import annotations

from alembic import op

revision = "0049"
down_revision = "0048_fix_guard_delete_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE combo_orders (
          id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
          account_id              UUID          NOT NULL REFERENCES broker_accounts(id),
          client_combo_id         TEXT          NOT NULL,
          strategy_type           TEXT          NOT NULL CHECK (strategy_type IN
                                      ('VERTICAL','CALENDAR','DIAGONAL','STRADDLE','STRANGLE')),
          underlying_symbol       TEXT          NOT NULL,
          underlying_canonical_id TEXT          NOT NULL,
          net_debit_credit        NUMERIC(20,8) NOT NULL,
          net_debit_credit_kind   TEXT          NOT NULL CHECK (net_debit_credit_kind IN ('DEBIT','CREDIT')),
          max_loss                NUMERIC(20,8) NULL,
          max_profit              NUMERIC(20,8) NULL,
          break_even              NUMERIC(20,8)[] NOT NULL DEFAULT '{}',
          tif                     TEXT          NOT NULL CHECK (tif IN ('DAY','GTC','IOC','FOK')),
          status                  TEXT          NOT NULL CHECK (status IN (
                                      'pending_submit','working','filled',
                                      'partially_filled','cancelled','rejected','legged_out')),
          broker_combo_id         TEXT          NULL,
          created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
          updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
          UNIQUE (account_id, client_combo_id)
        )
    """)
    op.execute("CREATE INDEX combo_orders_account_status_idx ON combo_orders (account_id, status)")
    op.execute("""
        CREATE TABLE order_legs (
          id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
          combo_id        UUID          NOT NULL REFERENCES combo_orders(id) ON DELETE CASCADE,
          order_id        UUID          NULL REFERENCES orders(id),
          leg_idx         SMALLINT      NOT NULL,
          instrument_id   BIGINT        NOT NULL REFERENCES instruments(id),
          side            TEXT          NOT NULL CHECK (side IN ('buy','sell')),
          ratio           SMALLINT      NOT NULL CHECK (ratio > 0) DEFAULT 1,
          qty             NUMERIC(20,8) NOT NULL,
          position_effect TEXT          NOT NULL CHECK (position_effect IN ('OPEN','CLOSE')),
          limit_price     NUMERIC(20,8) NULL,
          broker_order_id TEXT          NULL,
          filled_qty      NUMERIC(20,8) NOT NULL DEFAULT 0,
          avg_fill_price  NUMERIC(20,8) NULL,
          status          TEXT          NOT NULL DEFAULT 'pending_submit',
          UNIQUE (combo_id, leg_idx)
        )
    """)
    op.execute("CREATE INDEX order_legs_combo_idx ON order_legs (combo_id)")
    op.execute("CREATE INDEX order_legs_instrument_idx ON order_legs (instrument_id)")
    op.execute("""
        CREATE INDEX order_legs_broker_idx ON order_legs (broker_order_id)
          WHERE broker_order_id IS NOT NULL
    """)
    op.execute("ALTER TABLE orders ADD COLUMN combo_id UUID NULL REFERENCES combo_orders(id)")
    op.execute("""
        CREATE INDEX orders_combo_id_idx ON orders (combo_id)
          WHERE combo_id IS NOT NULL
    """)
    op.execute("ALTER TABLE risk_limits ADD COLUMN max_combo_loss_native NUMERIC(20,8) NULL")
    op.execute("ALTER TABLE risk_limits ADD COLUMN max_combo_net_delta NUMERIC(20,8) NULL")
    op.execute("ALTER TABLE risk_limits ADD COLUMN combo_legout_autoclose BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("""
        ALTER TABLE risk_decisions
          DROP CONSTRAINT risk_decisions_side_check,
          ADD CONSTRAINT risk_decisions_side_check
            CHECK (side IN ('buy','sell','combo'))
    """)
    op.execute("""
        ALTER TABLE risk_decisions
          DROP CONSTRAINT risk_decisions_attempt_kind_check,
          ADD CONSTRAINT risk_decisions_attempt_kind_check
            CHECK (attempt_kind IN (
              'preview','place_order','modify_order',
              'combo_preview','combo_place','combo_autoclose'
            ))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE risk_decisions
          DROP CONSTRAINT risk_decisions_attempt_kind_check,
          ADD CONSTRAINT risk_decisions_attempt_kind_check
            CHECK (attempt_kind IN ('preview','place_order','modify_order'))
    """)
    op.execute("""
        ALTER TABLE risk_decisions
          DROP CONSTRAINT risk_decisions_side_check,
          ADD CONSTRAINT risk_decisions_side_check
            CHECK (side IN ('buy','sell'))
    """)
    op.execute("ALTER TABLE risk_limits DROP COLUMN combo_legout_autoclose")
    op.execute("ALTER TABLE risk_limits DROP COLUMN max_combo_net_delta")
    op.execute("ALTER TABLE risk_limits DROP COLUMN max_combo_loss_native")
    op.execute("DROP INDEX IF EXISTS orders_combo_id_idx")
    op.execute("ALTER TABLE orders DROP COLUMN combo_id")
    op.execute("DROP TABLE order_legs")
    op.execute("DROP TABLE combo_orders")
