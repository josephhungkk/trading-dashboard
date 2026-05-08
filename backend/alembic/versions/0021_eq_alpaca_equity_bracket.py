"""Phase 8c T-B-eq.3 -- flip Alpaca STOCK BRACKET capability row.

Revision ID: 0021_eq_alpaca_equity_bracket
Revises: 0020a_alpaca_crypto_capability_flip
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing row safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_eq_alpaca_equity_bracket"
# Phase 9.6: re-pointed from 0020a to 0020b so the BRACKET row is seeded
# into order_types BEFORE this migration's INSERT into
# broker_order_capability hits the FK to order_types.code.
down_revision = "0020b_seed_bracket_order_type"
branch_labels = None
depends_on = None

CAPABILITY_ROW = ("alpaca", "STOCK", "BRACKET", "DAY")
NOTES = "Phase 8c T-B-eq.3 -- Alpaca equity bracket empirical PASS"


def upgrade() -> None:
    bind = op.get_bind()
    # Match 0018 concurrency discipline (chunk-C db MED-2).
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    broker_id, asset_class, order_type, tif = CAPABILITY_ROW
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
                    :b, :a, :o, :t, TRUE,
                    :notes,
                    NOW()
                )
            ON CONFLICT (broker_id, asset_class, order_type, time_in_force)
            DO UPDATE
                SET is_supported = TRUE,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """
        ),
        {
            "b": broker_id,
            "a": asset_class,
            "o": order_type,
            "t": tif,
            "notes": NOTES,
        },
    )
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    broker_id, asset_class, order_type, tif = CAPABILITY_ROW
    bind.execute(
        sa.text(
            """
            UPDATE broker_order_capability
               SET is_supported = FALSE,
                   notes = 'Reverted by Alembic 0021 downgrade',
                   updated_at = NOW()
             WHERE broker_id = :b
               AND asset_class = :a
               AND order_type = :o
               AND time_in_force = :t
            """
        ),
        {"b": broker_id, "a": asset_class, "o": order_type, "t": tif},
    )
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )
