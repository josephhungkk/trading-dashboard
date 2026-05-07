"""Phase 8c T-O.5 -- flip Alpaca OCO capability rows.

Revision ID: 0022_alpaca_oco_capability
Revises: 0021_cr_alpaca_crypto_bracket
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing row safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_alpaca_oco_capability"
down_revision = "0021_cr_alpaca_crypto_bracket"
branch_labels = None
depends_on = None

EQUITY_ROW = ("alpaca", "STOCK", "OCO", "GTC")
CRYPTO_ROW = ("alpaca", "CRYPTO", "OCO", "GTC")
EQUITY_NOTES = "Phase 8c equity OCO empirical PASS (T-O.5)"
CRYPTO_NOTES = "Alpaca crypto OCO not supported per Phase 8c empirical gate (T-O.5)"
DOWNGRADE_NOTES = "Reverted Phase 8c OCO capability"


def _upsert_capability(row: tuple[str, str, str, str], is_supported: bool, notes: str) -> None:
    bind = op.get_bind()
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
                    :b, :a, :o, :t,
                    :is_supported, :notes, NOW()
                )
            ON CONFLICT (broker_id, asset_class, order_type, time_in_force)
            DO UPDATE
                SET is_supported = EXCLUDED.is_supported,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """
        ),
        {
            "b": row[0],
            "a": row[1],
            "o": row[2],
            "t": row[3],
            "is_supported": is_supported,
            "notes": notes,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    _upsert_capability(EQUITY_ROW, True, EQUITY_NOTES)
    _upsert_capability(CRYPTO_ROW, False, CRYPTO_NOTES)
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            """
            UPDATE broker_order_capability
               SET is_supported = FALSE,
                   notes = :notes,
                   updated_at = NOW()
             WHERE broker_id = 'alpaca'
               AND order_type = 'OCO'
               AND time_in_force = 'GTC'
               AND asset_class IN ('STOCK', 'CRYPTO')
            """
        ),
        {"notes": DOWNGRADE_NOTES},
    )
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )
