"""Phase 9.5 retro: make oco_links.account_id FK ON DELETE RESTRICT explicit.

Resolves Phase 9.5 MED-db-1: the oco_links foreign key to broker_accounts was
created without an explicit ON DELETE action, defaulting to NO ACTION which can
produce confusing deferred errors. Making it RESTRICT clarifies the intent:
an account with active OCO links must not be deleted.

Revision ID: 0028b_oco_links_fk_explicit
Revises: 0028a_oco_links_unique_active
Create Date: 2026-05-08
"""
from __future__ import annotations

from alembic import op

revision = "0028b_oco_links_fk_explicit"
down_revision = "0028a_oco_links_unique_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("oco_links_account_id_fkey", "oco_links", type_="foreignkey")
    op.create_foreign_key(
        "oco_links_account_id_fkey",
        "oco_links",
        "broker_accounts",
        ["account_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("oco_links_account_id_fkey", "oco_links", type_="foreignkey")
    op.create_foreign_key(
        "oco_links_account_id_fkey",
        "oco_links",
        "broker_accounts",
        ["account_id"],
        ["id"],
        # Restore implicit NO ACTION (default) — matches original oco_links creation in 0016.
    )
