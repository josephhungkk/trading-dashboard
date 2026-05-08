"""Phase 9.5: updated_at triggers for positions and orders.

Revision ID: 0034a_phase9_5_positions_updated_at_trigger
Revises: 0034_phase9_5_order_events_fk_set_null
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op

revision = "0034a_phase9_5_positions_updated_at_trigger"
down_revision = "0034_phase9_5_order_events_fk_set_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS positions_updated_at ON positions;
        CREATE TRIGGER positions_updated_at
        BEFORE UPDATE ON positions
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at();

        DROP TRIGGER IF EXISTS orders_updated_at ON orders;
        CREATE TRIGGER orders_updated_at
        BEFORE UPDATE ON orders
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS orders_updated_at ON orders;
        DROP TRIGGER IF EXISTS positions_updated_at ON positions;
        """
    )
