"""combo_orders, order_legs updated_at triggers

Revision ID: 0049a_combo_orders_updated_at_trigger
Revises: 0049
"""

from __future__ import annotations

from alembic import op

revision = "0049a_combo_orders_updated_at_trigger"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS combo_orders_updated_at ON combo_orders")
    op.execute(
        """
        CREATE TRIGGER combo_orders_updated_at
        BEFORE UPDATE ON combo_orders
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS combo_orders_updated_at ON combo_orders")
