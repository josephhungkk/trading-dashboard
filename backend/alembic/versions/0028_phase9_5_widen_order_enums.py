"""Phase 9.5 retro: widen orders.order_type and orders.tif enums + drop stale CHECK.

Resolves Phase 9.5 CRIT-db-1: capability matrix rows for STOP_LIMIT/TRAIL/
TRAIL_LIMIT/MOC/MOO/LOC/LOO/IOC/FOK/GTD were flipped TRUE in Phase 8a (0011a)
and Phase 8b (0013/0014/0015) but the underlying enums were never extended.
Every order placement with these types fails at INSERT with
'invalid input value for enum order_type_enum' or 'order_tif_enum'.

Also drops the stale Phase 4 (0004) CHECK constraint that whitelists only
MARKET/LIMIT/STOP, replacing it with a NOT NULL check.

Revision ID: 0028_phase9_5_widen_order_enums
Revises: 0027
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0028_phase9_5_widen_order_enums"
down_revision = "0027"
branch_labels = None
depends_on = None

NEW_ORDER_TYPES = (
    "STOP_LIMIT",
    "TRAIL",
    "TRAIL_LIMIT",
    "MOC",
    "MOO",
    "LOC",
    "LOO",
)
NEW_TIFS = ("IOC", "FOK", "GTD")


def upgrade() -> None:
    conn = op.get_bind()
    for v in NEW_ORDER_TYPES:
        conn.execute(f"ALTER TYPE order_type_enum ADD VALUE IF NOT EXISTS '{v}'")
    for v in NEW_TIFS:
        conn.execute(f"ALTER TYPE order_tif_enum ADD VALUE IF NOT EXISTS '{v}'")
    # Drop the stale 0004 CHECK that whitelists only MARKET/LIMIT/STOP.
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_order_type_check")
    # Replace with a NOT NULL guard (type system now enforces the value set).
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_order_type_check "
        "CHECK (order_type IS NOT NULL)"
    )


def downgrade() -> None:
    # Restore original narrow CHECK — note: added enum values cannot be removed
    # from PG enum types; downgrade only restores the application-level constraint.
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_order_type_check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_order_type_check "
        "CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP'))"
    )
