"""Phase 8b T-I.3 -- flip IBKR capability rows for trail, auction, and extended TIF families.

Revision ID: 0015_ibkr_capability_flip
Revises: 0014_futu_capability_flip
Create Date: 2026-05-06

Adds Phase 8b order-type expansion for IBKR broker. Covers:
  - Trail family (TRAIL, TRAIL_LIMIT) across DAY/GTC/IOC/GTD
  - Auction-session orders (MOC, MOO, LOC, LOO) -- DAY only per spec invariant
  - MARKET extended TIFs (GTC, IOC, FOK)
  - LIMIT extended TIFs (IOC, FOK, GTD)
  - STOP/STOP_LIMIT GTC + GTD
  - IBKR supports broad GTD across LIMIT/STOP/TRAIL families

ON CONFLICT handles any pre-existing rows safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_ibkr_capability_flip"
down_revision = "0014a_futu_revert_unsupported_tif"
branch_labels = None
depends_on = None

CAPABILITY_ROWS = [
    # Trail family -- full universe
    ("ibkr", "TRAIL", "DAY"),
    ("ibkr", "TRAIL", "GTC"),
    ("ibkr", "TRAIL", "IOC"),
    ("ibkr", "TRAIL_LIMIT", "DAY"),
    ("ibkr", "TRAIL_LIMIT", "GTC"),
    # Auction-session orders (DAY-only by spec invariant)
    ("ibkr", "MOC", "DAY"),
    ("ibkr", "MOO", "DAY"),
    ("ibkr", "LOC", "DAY"),
    ("ibkr", "LOO", "DAY"),
    # MARKET extended TIFs
    ("ibkr", "MARKET", "GTC"),
    ("ibkr", "MARKET", "IOC"),
    ("ibkr", "MARKET", "FOK"),
    # LIMIT extended TIFs
    ("ibkr", "LIMIT", "IOC"),
    ("ibkr", "LIMIT", "FOK"),
    # STOP family GTC
    ("ibkr", "STOP", "GTC"),
    ("ibkr", "STOP_LIMIT", "GTC"),
    # GTD across LIMIT/STOP family (IBKR supports broad GTD)
    ("ibkr", "LIMIT", "GTD"),
    ("ibkr", "STOP", "GTD"),
    ("ibkr", "STOP_LIMIT", "GTD"),
    ("ibkr", "TRAIL", "GTD"),
    ("ibkr", "TRAIL_LIMIT", "GTD"),
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
                    (:b, :o, :t, TRUE, 'Phase 8b -- flipped via Alembic 0015', NOW())
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
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'ibkr')"
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
                       notes = 'Reverted by Alembic 0015 downgrade',
                       updated_at = NOW()
                 WHERE broker_id = :b AND order_type = :o AND time_in_force = :t
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'ibkr')"
        )
    )
