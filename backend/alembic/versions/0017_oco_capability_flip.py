"""Phase 8b T-O.12 -- flip broker_features.is_supported=TRUE for OCO across all 3 brokers.

Revision ID: 0017_oco_capability_flip
Revises: 0016_oco_links
Create Date: 2026-05-06

After empirical gates T-O.10 (Schwab) + T-O.11 (Futu orchestrated) PASS, flip
broker_features.is_supported=TRUE for feature='oco' on schwab/ibkr/futu.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_oco_capability_flip"
down_revision = "0016_oco_links"
branch_labels = None
depends_on = None

OCO_BROKERS = ("schwab", "ibkr", "futu")


def upgrade() -> None:
    bind = op.get_bind()
    for broker_id in OCO_BROKERS:
        bind.execute(
            sa.text(
                "UPDATE broker_features"
                " SET is_supported = TRUE,"
                " notes = 'Phase 8b T-O.12 -- OCO validated (T-O.10 schwab + T-O.11 futu)',"
                " updated_at = NOW()"
                " WHERE broker_id = :b AND feature = 'oco'"
            ),
            {"b": broker_id},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'oco')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for broker_id in OCO_BROKERS:
        bind.execute(
            sa.text(
                "UPDATE broker_features"
                " SET is_supported = FALSE,"
                " notes = 'Reverted by 0017 downgrade',"
                " updated_at = NOW()"
                " WHERE broker_id = :b AND feature = 'oco'"
            ),
            {"b": broker_id},
        )
    bind.execute(
        sa.text(
            "SELECT pg_notify('app_config:invalidate:order_capabilities', 'oco')"
        )
    )
