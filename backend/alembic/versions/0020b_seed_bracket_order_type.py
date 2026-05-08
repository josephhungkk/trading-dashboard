"""Phase 9.6 — seed BRACKET into order_types.

Revision ID: 0020b_seed_bracket_order_type
Revises: 0020a_alpaca_crypto_capability_flip
Create Date: 2026-05-08

0021_eq_alpaca_equity_bracket and 0021_cr_alpaca_crypto_bracket both
INSERT into broker_order_capability with order_type='BRACKET'. The FK
broker_order_capability.order_type -> order_types.code rejects this
because BRACKET was never seeded into order_types (0011 only seeded
the 10 simple types: MARKET, LIMIT, STOP, STOP_LIMIT, TRAIL,
TRAIL_LIMIT, MOC, MOO, LOC, LOO).

This migration adds BRACKET to order_types so the 0021 sibling
migrations can succeed. Idempotent via ON CONFLICT DO NOTHING — safe
to apply on any DB regardless of prior state.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020b_seed_bracket_order_type"
down_revision = "0020a_alpaca_crypto_capability_flip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO order_types (code, label, description, sort_order)
            VALUES (
                'BRACKET',
                'Bracket',
                'Parent entry with attached take-profit and stop-loss children.',
                110
            )
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Only delete the row if no broker_order_capability still references
    # it; otherwise leave it (downgrading from a state where 0021_eq /
    # 0021_cr already inserted bracket capability rows would fail the FK
    # via RESTRICT, which is the correct safety behavior).
    op.execute(
        sa.text(
            """
            DELETE FROM order_types
             WHERE code = 'BRACKET'
               AND NOT EXISTS (
                   SELECT 1
                     FROM broker_order_capability
                    WHERE order_type = 'BRACKET'
               )
            """
        )
    )
