"""Phase 8c T-S.8 -- flip Alpaca STOCK capability rows.

Revision ID: 0020_alpaca_equity_capability_flip
Revises: 0019_widen_qty_to_10dp
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing rows safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_alpaca_equity_capability_flip"
down_revision = "0019_widen_qty_to_10dp"
branch_labels = None
depends_on = None

CAPABILITY_ROWS = [
    # Alpaca REST v2 rejects MARKET orders with IOC/FOK on equity (only
    # day/gtc accepted). IOC/FOK are LIMIT-only — see chunk-S DB review.
    ("alpaca", "STOCK", "MARKET", "DAY"),
    ("alpaca", "STOCK", "MARKET", "GTC"),
    ("alpaca", "STOCK", "LIMIT", "DAY"),
    ("alpaca", "STOCK", "LIMIT", "GTC"),
    ("alpaca", "STOCK", "LIMIT", "IOC"),
    ("alpaca", "STOCK", "LIMIT", "FOK"),
    ("alpaca", "STOCK", "STOP", "DAY"),
    ("alpaca", "STOCK", "STOP", "GTC"),
    ("alpaca", "STOCK", "STOP_LIMIT", "DAY"),
    ("alpaca", "STOCK", "STOP_LIMIT", "GTC"),
    ("alpaca", "STOCK", "TRAIL", "DAY"),
    ("alpaca", "STOCK", "TRAIL", "GTC"),
    ("alpaca", "STOCK", "MOC", "DAY"),
    ("alpaca", "STOCK", "MOO", "DAY"),
    ("alpaca", "STOCK", "LOC", "DAY"),
    ("alpaca", "STOCK", "LOO", "DAY"),
]


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
                        'Phase 8c T-S.8 -- flipped via Alembic 0020',
                        NOW()
                    )
                ON CONFLICT (broker_id, asset_class, order_type, time_in_force)
                DO UPDATE
                    SET is_supported = TRUE,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                """
            ),
            {"b": broker_id, "a": asset_class, "o": order_type, "t": tif},
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
                       notes = 'Reverted by Alembic 0020 downgrade',
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
