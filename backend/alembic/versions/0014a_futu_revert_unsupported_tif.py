"""Phase 8b T-F.1+F.2+F.3 follow-up -- revert IOC/FOK/GTD on Futu.

Revision ID: 0014a_futu_revert_unsupported_tif
Revises: 0014_futu_capability_flip
Create Date: 2026-05-06

Empirical SDK inspection (T-F.1+F.2+F.3) confirmed Futu's `ft.TimeInForce` enum
only has DAY and GTC. The plan template's IOC/FOK/GTD rows in 0014 were
incorrect -- the sidecar normalize layer raises NotImplementedError for those
TIFs. Revert those three rows to is_supported=FALSE so the capability gate
rejects them at the boundary instead of letting the order reach the sidecar.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014a_futu_revert_unsupported_tif"
down_revision = "0014_futu_capability_flip"
branch_labels = None
depends_on = None

REVERT_ROWS = [
    ("futu", "LIMIT", "IOC"),
    ("futu", "LIMIT", "FOK"),
    ("futu", "LIMIT", "GTD"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in REVERT_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_order_capability
                   SET is_supported = FALSE,
                       notes = 'Reverted -- futu SDK TimeInForce enum only has DAY/GTC',
                       updated_at = NOW()
                 WHERE broker_id = :b AND order_type = :o AND time_in_force = :t
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'futu')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for broker_id, order_type, tif in REVERT_ROWS:
        bind.execute(
            sa.text(
                """
                UPDATE broker_order_capability
                   SET is_supported = TRUE,
                       notes = 'Restored by 0014a downgrade',
                       updated_at = NOW()
                 WHERE broker_id = :b AND order_type = :o AND time_in_force = :t
                """
            ),
            {"b": broker_id, "o": order_type, "t": tif},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'futu')"
        )
    )
