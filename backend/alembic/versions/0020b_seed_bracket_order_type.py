"""Phase 9.6 — seed BRACKET + OCO into order_types.

Revision ID: 0020b_seed_bracket_order_type
Revises: 0020a_alpaca_crypto_capability_flip
Create Date: 2026-05-08

0021_eq + 0021_cr INSERT into broker_order_capability with
order_type='BRACKET'; 0022_alpaca_oco_capability inserts
order_type='OCO'. The FK broker_order_capability.order_type ->
order_types.code rejects both because neither was ever seeded into
order_types (0011 only seeded the 10 simple types: MARKET, LIMIT,
STOP, STOP_LIMIT, TRAIL, TRAIL_LIMIT, MOC, MOO, LOC, LOO).

This migration adds both rows so the 0021/0022 sibling migrations
can succeed. Idempotent via ON CONFLICT DO NOTHING — safe to apply
on any DB regardless of prior state.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020b_seed_bracket_order_type"
down_revision = "0020a_alpaca_crypto_capability_flip"
branch_labels = None
depends_on = None

_SEED_ROWS = [
    (
        "BRACKET",
        "Bracket",
        "Parent entry with attached take-profit and stop-loss children.",
        110,
    ),
    (
        "OCO",
        "One-Cancels-Other",
        "Two linked orders where filling one auto-cancels the other.",
        120,
    ),
]


def upgrade() -> None:
    for code, label, description, sort_order in _SEED_ROWS:
        op.execute(
            sa.text(
                """
                INSERT INTO order_types (code, label, description, sort_order)
                VALUES (:code, :label, :description, :sort_order)
                ON CONFLICT (code) DO NOTHING
                """
            ).bindparams(
                code=code,
                label=label,
                description=description,
                sort_order=sort_order,
            )
        )


def downgrade() -> None:
    # Only delete each row if no broker_order_capability still
    # references it; otherwise leave it (downgrading from a state where
    # 0021_eq / 0021_cr / 0022 already inserted capability rows would
    # fail the FK via RESTRICT, which is the correct safety behavior).
    for code, _label, _description, _sort_order in _SEED_ROWS:
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
            ).bindparams(code=code)
        )
