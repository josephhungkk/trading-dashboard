"""Phase 8b T-S.3 -- flip Schwab capability rows for trail, auction-session, and GTD order types.

Revision ID: 0013_schwab_capability_flip
Revises: 0012_broker_features
Create Date: 2026-05-06

Adds Phase 8b order-type expansion on top of the 16 rows already flipped
by 0011a. Covers:
  - Trail family (TRAIL / TRAIL_LIMIT) across DAY + GTC
  - Auction-session orders (MOC / MOO / LOC / LOO) — DAY only per spec invariant
  - GTD across the full LIMIT family (LIMIT/STOP/STOP_LIMIT/TRAIL/TRAIL_LIMIT)

MARKET+GTD is excluded (Schwab does not support it per spec).
ON CONFLICT handles any pre-existing rows safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_schwab_capability_flip"
down_revision = "0012_broker_features"
branch_labels = None
depends_on = None

ROWS = [
    # Trail family — DAY + GTC
    ("schwab", "TRAIL",       "DAY"),
    ("schwab", "TRAIL",       "GTC"),
    ("schwab", "TRAIL_LIMIT", "DAY"),
    ("schwab", "TRAIL_LIMIT", "GTC"),
    # Auction-session orders — DAY only (spec invariant)
    ("schwab", "MOC",         "DAY"),
    ("schwab", "MOO",         "DAY"),
    ("schwab", "LOC",         "DAY"),
    ("schwab", "LOO",         "DAY"),
    # GTD across the supported LIMIT family
    ("schwab", "LIMIT",       "GTD"),
    ("schwab", "STOP",        "GTD"),
    ("schwab", "STOP_LIMIT",  "GTD"),
    ("schwab", "TRAIL",       "GTD"),
    ("schwab", "TRAIL_LIMIT", "GTD"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in ROWS:
        bind.execute(
            sa.text(
                """
                INSERT INTO broker_order_capability
                    (broker_id, order_type, time_in_force, is_supported, notes, updated_at)
                VALUES
                    (:b, :o, :t, TRUE, 'Phase 8b -- flipped via Alembic 0013', NOW())
                ON CONFLICT (broker_id, order_type, time_in_force) DO UPDATE
                    SET is_supported = TRUE,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'schwab')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_order_capability
                   SET is_supported = FALSE,
                       notes = 'Reverted by Alembic 0013 downgrade',
                       updated_at = NOW()
                 WHERE broker_id = :b
                   AND order_type = :o
                   AND time_in_force = :t
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
