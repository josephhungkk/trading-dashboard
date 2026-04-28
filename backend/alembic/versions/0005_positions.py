"""positions table for per-account holdings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-28

Phase 5b.1 — surfaces broker positions per (account, conid) for the
order-preview position-sanity check, frontend portfolio widgets, and
future Phase 5c bracket-order math. Discoverer fan-out (Phase 5a
pattern) populates this table on its 30s tick.

avg_cost is per-share; multiplier comes from the Contract proto (50
for futures, 100 for options, 1 for stocks). notional = qty * avg_cost
* multiplier.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE positions (
          account_id    UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
          conid         VARCHAR(32) NOT NULL,
          qty           NUMERIC(20,8) NOT NULL,
          avg_cost      NUMERIC(20,8) NOT NULL,
          currency      VARCHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
          multiplier    NUMERIC(20,8) NOT NULL DEFAULT 1,
          asset_class   VARCHAR(16)   NOT NULL DEFAULT 'STOCK',
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (account_id, conid)
        );
        """
    )
    op.execute("CREATE INDEX positions_account_id_idx ON positions(account_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS positions_account_id_idx;")
    op.execute("DROP TABLE IF EXISTS positions;")
