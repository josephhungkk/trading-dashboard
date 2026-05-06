"""Phase 8a A5 -- flip Schwab capability rows after C0 empirical gate passes.

Revision ID: 0011a_phase8a_schwab_flip
Revises: 0011_phase8a_order_capability
Create Date: 2026-05-06

C0 empirical hard-gate PASSED on 2026-05-06T15:56Z (artifact:
scripts/empirical/artifacts/schwab_c0_20260506T155600Z.json). All 8
assertions green: place 201 + Location header, broker_order_id round-trip,
canonical status set, cancel 200. Unblocks this flip.

Flips the 16 (order_type, time_in_force) combos that the Phase 8a sidecar
+ to_schwab_order_payload actually supports today: MARKET/LIMIT/STOP/STOP_LIMIT
across DAY/GTC/IOC/FOK. The remaining 34 Schwab rows (TRAIL, TRAIL_LIMIT,
MOC, MOO, LOC, LOO, plus all GTD combos) stay unsupported and land in
Phase 8b's order-type expansion.
"""

from __future__ import annotations

from alembic import op

revision = "0011a_phase8a_schwab_flip"
down_revision = "0011_phase8a_order_capability"
branch_labels = None
depends_on = None

SUPPORTED_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
SUPPORTED_TIFS = ("DAY", "GTC", "IOC", "FOK")


def upgrade() -> None:
    types_csv = ",".join(f"'{t}'" for t in SUPPORTED_TYPES)
    tifs_csv = ",".join(f"'{f}'" for f in SUPPORTED_TIFS)
    op.execute(
        f"""
        UPDATE broker_order_capability
           SET is_supported = TRUE,
               notes = '',
               updated_at = NOW()
         WHERE broker_id = 'schwab'
           AND order_type IN ({types_csv})
           AND time_in_force IN ({tifs_csv})
        """
    )
    op.execute(
        "SELECT pg_notify('app_config:invalidate:order_capabilities', 'schwab')"
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE broker_order_capability
           SET is_supported = FALSE,
               notes = 'Reverted by 0011a downgrade',
               updated_at = NOW()
         WHERE broker_id = 'schwab'
        """
    )
    op.execute(
        "SELECT pg_notify('app_config:invalidate:order_capabilities', 'schwab')"
    )
