"""add account_id to bot_orders

Revision ID: 0061a
Revises: 0061
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0061a"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_orders",
        sa.Column("account_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bot_orders_account_id",
        "bot_orders",
        "broker_accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bot_orders_account_id",
        "bot_orders",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_orders_account_id", table_name="bot_orders")
    op.drop_constraint("fk_bot_orders_account_id", "bot_orders", type_="foreignkey")
    op.drop_column("bot_orders", "account_id")
