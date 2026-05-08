"""Idempotent OCO seed inserted between 0021_cr and 0022.

Phase 9.6 added 0020b_seed_bracket_order_type with only BRACKET. That
migration ran on prod before OCO was added to its `_SEED_ROWS` (commit
ac56672), so prod's `order_types` table is missing OCO. Migration 0022
then fails its FK on `broker_order_capability.order_type='OCO'`.

This migration seeds OCO idempotently and is chained immediately
before 0022. Dev DBs that already have OCO via the extended 0020b are
unaffected (`ON CONFLICT (code) DO NOTHING`).

Revision ID: 0021d_seed_oco_order_type
Revises: 0021_cr_alpaca_crypto_bracket
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021d_seed_oco_order_type"
down_revision = "0021_cr_alpaca_crypto_bracket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO order_types (code, label, description, sort_order)
            VALUES (:code, :label, :description, :sort_order)
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(
            code="OCO",
            label="One-Cancels-Other",
            description="Two linked orders where filling one auto-cancels the other.",
            sort_order=120,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM order_types
             WHERE code = :code
               AND NOT EXISTS (
                   SELECT 1
                     FROM broker_order_capability
                    WHERE order_type = :code
               )
            """
        ).bindparams(code="OCO")
    )
