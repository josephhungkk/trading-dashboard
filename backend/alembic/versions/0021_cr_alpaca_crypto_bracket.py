"""Phase 8c T-B-cr.2 -- mark Alpaca CRYPTO BRACKET unsupported.

Revision ID: 0021_cr_alpaca_crypto_bracket
Revises: 0021_eq_alpaca_equity_bracket
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing row safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_cr_alpaca_crypto_bracket"
down_revision = "0021_eq_alpaca_equity_bracket"
branch_labels = None
depends_on = None

CAPABILITY_ROW = ("alpaca", "CRYPTO", "BRACKET", "DAY")
NOTES = "Alpaca crypto bracket not supported per Phase 8c empirical gate (T-B-cr.1)"
DOWNGRADE_NOTES = "Reverted Phase 8c crypto bracket negative capability"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO broker_order_capability
                (
                    broker_id, asset_class, order_type, time_in_force,
                    is_supported, notes, updated_at
                )
            VALUES
                (
                    :b, :a, :o, :t,
                    FALSE, :notes, NOW()
                )
            ON CONFLICT (broker_id, asset_class, order_type, time_in_force)
            DO UPDATE
                SET is_supported = FALSE,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """
        ),
        {
            "b": CAPABILITY_ROW[0],
            "a": CAPABILITY_ROW[1],
            "o": CAPABILITY_ROW[2],
            "t": CAPABILITY_ROW[3],
            "notes": NOTES,
        },
    )
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE broker_order_capability
               SET notes = :notes,
                   updated_at = NOW()
             WHERE broker_id = :b
               AND asset_class = :a
               AND order_type = :o
               AND time_in_force = :t
            """
        ),
        {
            "b": CAPABILITY_ROW[0],
            "a": CAPABILITY_ROW[1],
            "o": CAPABILITY_ROW[2],
            "t": CAPABILITY_ROW[3],
            "notes": DOWNGRADE_NOTES,
        },
    )
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )
