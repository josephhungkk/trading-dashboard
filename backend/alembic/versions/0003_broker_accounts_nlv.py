"""broker_accounts_nlv

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "broker_accounts_last_nlv_currency_iso3",
        "broker_accounts",
        "last_nlv_currency IS NULL OR last_nlv_currency ~ '^[A-Z]{3}$'",
    )


def downgrade() -> None:
    op.drop_constraint("broker_accounts_last_nlv_currency_iso3", "broker_accounts", type_="check")
    op.drop_column("broker_accounts", "last_nlv_at")
    op.drop_column("broker_accounts", "last_nlv_currency")
    op.drop_column("broker_accounts", "last_nlv")
