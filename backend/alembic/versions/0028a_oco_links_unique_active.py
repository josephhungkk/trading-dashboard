"""Phase 9.5 retro: prevent same broker order in 2 active OCO groups.

Resolves Phase 9.5 HIGH-db-1: oco_links had no uniqueness constraint preventing
the same broker order_id from appearing in multiple active OCO groups simultaneously.
Partial unique indexes on (broker_id, order_id_a) and (broker_id, order_id_b)
scoped to non-terminal rows prevent duplicates while allowing reuse after completion.

Revision ID: 0028a_oco_links_unique_active
Revises: 0028_phase9_5_widen_order_enums
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0028a_oco_links_unique_active"
down_revision = "0028_phase9_5_widen_order_enums"
branch_labels = None
depends_on = None

_TERMINAL_STATUSES = "('COMPLETED','CANCELED','ERROR','CANCEL_FAILED')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_oco_links_order_id_a_active
          ON oco_links(broker_id, order_id_a)
          WHERE status NOT IN {_TERMINAL_STATUSES}
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_oco_links_order_id_b_active
          ON oco_links(broker_id, order_id_b)
          WHERE status NOT IN {_TERMINAL_STATUSES}
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oco_links_order_id_a_active")
    op.execute("DROP INDEX IF EXISTS uq_oco_links_order_id_b_active")
