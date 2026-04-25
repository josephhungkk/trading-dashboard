"""broker_accounts table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


broker_id_enum = postgresql.ENUM(
    "ibkr",
    "futu",
    "schwab",
    name="broker_id_enum",
    create_type=False,
)
trading_mode_enum = postgresql.ENUM(
    "live",
    "paper",
    name="trading_mode_enum",
    create_type=False,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE TYPE broker_id_enum AS ENUM ('ibkr', 'futu', 'schwab')")
    op.execute("CREATE TYPE trading_mode_enum AS ENUM ('live', 'paper')")

    op.create_table(
        "broker_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("broker_id", broker_id_enum, nullable=False),
        sa.Column("account_number", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=True),
        sa.Column("mode", trading_mode_enum, nullable=False),
        sa.Column("gateway_label", sa.Text(), nullable=False),
        sa.Column("currency_base", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_via", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "broker_id",
            "account_number",
            name="broker_accounts_natural_uq",
        ),
    )
    op.create_index(
        "ix_broker_accounts_active",
        "broker_accounts",
        ["broker_id", "mode"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_broker_accounts_active")
    op.execute("DROP TABLE IF EXISTS broker_accounts")
    op.execute("DROP TYPE IF EXISTS trading_mode_enum")
    op.execute("DROP TYPE IF EXISTS broker_id_enum")
