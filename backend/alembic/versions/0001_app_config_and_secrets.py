"""app_config and app_secrets tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("namespace", "key"),
        sa.CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_config_value_type_check",
        ),
        sa.CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value IS NULL)"
            " OR "
            "(value_type <> 'json' AND value IS NOT NULL AND value_json IS NULL)",
            name="app_config_value_exclusive",
        ),
    )
    op.create_index(
        "ix_app_config_updated_at",
        "app_config",
        [sa.text("updated_at DESC")],
    )

    op.create_table(
        "app_secrets",
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("value_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("namespace", "key"),
        sa.CheckConstraint(
            "value_type IN ('str','int','bool','json')",
            name="app_secrets_value_type_check",
        ),
    )
    op.create_index(
        "ix_app_secrets_updated_at",
        "app_secrets",
        [sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_app_secrets_updated_at", table_name="app_secrets")
    op.drop_table("app_secrets")
    op.drop_index("ix_app_config_updated_at", table_name="app_config")
    op.drop_table("app_config")
