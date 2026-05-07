"""Phase 8c T-C.6 -- flip Alpaca CRYPTO capability rows.

Revision ID: 0020a_alpaca_crypto_capability_flip
Revises: 0020_alpaca_equity_capability_flip
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing rows safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020a_alpaca_crypto_capability_flip"
down_revision = "0020_alpaca_equity_capability_flip"
branch_labels = None
depends_on = None

CAPABILITY_ROWS = [
    # Alpaca crypto docs list market, limit, and stop_limit support. Keep this
    # migration to the empirical PASS branch confirmed for Phase 8c T-C.6.
    ("alpaca", "CRYPTO", "MARKET", "DAY"),
    ("alpaca", "CRYPTO", "MARKET", "GTC"),
    ("alpaca", "CRYPTO", "LIMIT", "DAY"),
    ("alpaca", "CRYPTO", "LIMIT", "GTC"),
]

NOTES = "Phase 8c T-C.6 -- Alpaca crypto empirical PASS (MED-4)"


def upgrade() -> None:
    bind = op.get_bind()
    for broker_id, asset_class, order_type, tif in CAPABILITY_ROWS:
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
    for broker_id, asset_class, order_type, tif in CAPABILITY_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_order_capability
                   SET is_supported = FALSE,
                       notes = 'Reverted by Alembic 0020a downgrade',
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
