"""Idempotent seed for OCO order_type — recovers VPS prod from FK violation.

Phase 9.6 added migration 0020b_seed_bracket_order_type with only BRACKET
in `_SEED_ROWS`; that migration ran on prod before OCO was added to the
list (in commit ac56672). Alembic does not re-run a migration once it's
in alembic_version, so prod's `order_types` table was missing the OCO row
when migration 0022_alpaca_oco_capability tried to INSERT a
broker_order_capability with order_type='OCO' — failing the FK to
order_types.code.

This migration runs after 0035 (current head) and seeds OCO with
ON CONFLICT DO NOTHING, so dev DBs that already received the OCO row
via the extended 0020b are unaffected.

Revision ID: 0036_seed_oco_order_type
Revises: 0035_phase9_5_nlv_at_index
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036_seed_oco_order_type"
down_revision = "0035_phase9_5_nlv_at_index"
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
