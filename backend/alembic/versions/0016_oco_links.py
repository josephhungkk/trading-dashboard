"""Phase 8b T-O.1 -- create oco_links table for two-leg OCO order lifecycle tracking.

Revision ID: 0016_oco_links
Revises: 0015_ibkr_capability_flip
Create Date: 2026-05-06

Adds the oco_links table that tracks the lifecycle of two-leg OCO orders.
Each row records both leg order IDs, the current status, and which leg filled
(if any), enabling the OCO controller to cancel the sibling after a fill.

Status values (enforced by CHECK constraint oco_status_check):
  PENDING_BOTH    -- both legs submitted, neither working yet
  LEG_A_WORKING   -- leg A is live and working
  LEG_B_WORKING   -- leg B is live and working
  LEG_A_FILLED    -- leg A filled; sibling cancel in progress
  LEG_B_FILLED    -- leg B filled; sibling cancel in progress
  CANCELED        -- both legs canceled (terminal)
  CANCEL_FAILED   -- attempted sibling cancel but broker rejected
  ERROR           -- unexpected error state
  COMPLETED       -- OCO lifecycle complete (terminal)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_oco_links"
down_revision = "0015_ibkr_capability_flip"
branch_labels = None
depends_on = None

_STATUS_CHECK = (
    "status IN ("
    "'PENDING_BOTH','LEG_A_WORKING','LEG_B_WORKING',"
    "'LEG_A_FILLED','LEG_B_FILLED',"
    "'CANCELED','CANCEL_FAILED','ERROR','COMPLETED'"
    ")"
)


def upgrade() -> None:
    # pgcrypto provides gen_random_uuid(); safe to run if already present.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "oco_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("broker_id", sa.Text(), nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_accounts.id"),
            nullable=False,
        ),
        sa.Column("order_id_a", sa.Text(), nullable=False),
        sa.Column("order_id_b", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="PENDING_BOTH",
        ),
        sa.Column("filled_leg_id", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(_STATUS_CHECK, name="oco_status_check"),
    )

    op.create_index("idx_oco_links_account", "oco_links", ["account_id"])

    # Partial index cannot be expressed via op.create_index; use raw DDL.
    op.execute(
        sa.text(
            "CREATE INDEX idx_oco_links_status ON oco_links(status) "
            "WHERE status NOT IN ('COMPLETED','CANCELED')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_oco_links_status"))
    op.drop_index("idx_oco_links_account", table_name="oco_links")
    op.drop_table("oco_links")
    # Do NOT drop pgcrypto -- it may be used by other tables.
