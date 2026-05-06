"""Phase 8b T-F.5 -- flip Futu capability rows for trail, stop, IOC/FOK/GTD, and enable modify/bracket.

Revision ID: 0014_futu_capability_flip
Revises: 0013_schwab_capability_flip
Create Date: 2026-05-06

Adds Phase 8b order-type expansion for Futu broker. Covers:
  - Trail family (TRAIL) across DAY + GTC (HK has no GTD on trail)
  - IOC/FOK on LIMIT (supported on HKEX)
  - Stop family (STOP, STOP_LIMIT) across DAY + GTC
  - GTD on LIMIT (conservative; verified via T-F.3 normalize logic)

Also flips broker_features.is_supported for 'modify' and 'bracket' to TRUE (T-F.1 + T-F.2).
ON CONFLICT handles any pre-existing rows safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_futu_capability_flip"
down_revision = "0013_schwab_capability_flip"
branch_labels = None
depends_on = None

CAPABILITY_ROWS = [
    # Trail family — DAY + GTC only (HK has no GTD on trail)
    ("futu", "TRAIL", "DAY"),
    ("futu", "TRAIL", "GTC"),
    # IOC / FOK on LIMIT (Futu supports these for HKEX)
    ("futu", "LIMIT", "IOC"),
    ("futu", "LIMIT", "FOK"),
    # Stop family DAY + GTC
    ("futu", "STOP", "DAY"),
    ("futu", "STOP", "GTC"),
    ("futu", "STOP_LIMIT", "DAY"),
    ("futu", "STOP_LIMIT", "GTC"),
    # GTD on LIMIT (conservative; empirically verified in T-F.3)
    ("futu", "LIMIT", "GTD"),
]

FEATURE_ROWS = [
    ("futu", "modify", True),   # T-F.1 enabled live path
    ("futu", "bracket", True),  # T-F.2 enabled live path
]


def upgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in CAPABILITY_ROWS:
        bind.execute(
            sa.text(
                """
                INSERT INTO broker_order_capability
                    (broker_id, order_type, time_in_force, is_supported, notes, updated_at)
                VALUES
                    (:b, :o, :t, TRUE, 'Phase 8b -- flipped via Alembic 0014', NOW())
                ON CONFLICT (broker_id, order_type, time_in_force) DO UPDATE
                    SET is_supported = TRUE,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    for broker_id, feature, val in FEATURE_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_features
                   SET is_supported = :v,
                       notes = 'Phase 8b T-F.5 -- modify/bracket enabled',
                       updated_at = NOW()
                 WHERE broker_id = :b AND feature = :f
                """
            ),
            {"b": broker_id, "f": feature, "v": val},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'futu')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in CAPABILITY_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_order_capability
                   SET is_supported = FALSE,
                       notes = 'Reverted by Alembic 0014 downgrade',
                       updated_at = NOW()
                 WHERE broker_id = :b AND order_type = :o AND time_in_force = :t
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    for broker_id, feature, _val in FEATURE_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_features
                   SET is_supported = FALSE,
                       notes = 'Reverted by Alembic 0014 downgrade',
                       updated_at = NOW()
                 WHERE broker_id = :b AND feature = :f
                """
            ),
            {"b": broker_id, "f": feature},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'futu')"
        )
    )
