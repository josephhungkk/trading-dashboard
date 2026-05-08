"""Phase 8c T-O.5 -- flip Alpaca OCO capability rows.

Revision ID: 0022_alpaca_oco_capability
Revises: 0021d_seed_oco_order_type
Create Date: 2026-05-07

ON CONFLICT handles any pre-existing row safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_alpaca_oco_capability"
down_revision = "0021d_seed_oco_order_type"
branch_labels = None
depends_on = None

EQUITY_ROW = ("alpaca", "STOCK", "OCO", "GTC")
CRYPTO_ROW = ("alpaca", "CRYPTO", "OCO", "GTC")
EQUITY_NOTES = "Phase 8c equity OCO empirical PASS (T-O.5)"
CRYPTO_NOTES = "Alpaca crypto OCO not supported per Phase 8c empirical gate (T-O.5)"
EQUITY_DOWNGRADE_NOTES = "Reverted Phase 8c equity OCO (was TRUE)"
CRYPTO_DOWNGRADE_NOTES = "Reverted Phase 8c crypto OCO (already FALSE)"


def _upsert_capability(
    bind: sa.engine.Connection,
    row: tuple[str, str, str, str],
    is_supported: bool,
    notes: str,
) -> None:
    # bind is passed in (chunk-OCO db M-2) so the helper's connection
    # dependency is explicit and won't silently re-acquire op.get_bind() if
    # moved outside this migration module.
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


def _update_capability_notes(
    bind: sa.engine.Connection,
    row: tuple[str, str, str, str],
    notes: str,
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE broker_order_capability
               SET is_supported = FALSE,
                   notes = :notes,
                   updated_at = NOW()
             WHERE broker_id = :b
               AND asset_class = :a
               AND order_type = :o
               AND time_in_force = :t
            """
        ),
        {
            "b": row[0],
            "a": row[1],
            "o": row[2],
            "t": row[3],
            "notes": notes,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    _upsert_capability(bind, EQUITY_ROW, True, EQUITY_NOTES)
    _upsert_capability(bind, CRYPTO_ROW, False, CRYPTO_NOTES)
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE broker_order_capability IN SHARE ROW EXCLUSIVE MODE")
    )
    # Per-row notes (chunk-OCO db M-1) keep the audit trail clear: equity
    # reverts from TRUE → FALSE; crypto stays FALSE (already negative).
    _update_capability_notes(bind, EQUITY_ROW, EQUITY_DOWNGRADE_NOTES)
    _update_capability_notes(bind, CRYPTO_ROW, CRYPTO_DOWNGRADE_NOTES)
    bind.execute(
        sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', 'alpaca')")
    )
