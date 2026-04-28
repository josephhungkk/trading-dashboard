"""modified enum value (split from advanced orders)

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-28

Postgres restriction: ALTER TYPE ... ADD VALUE cannot be referenced in
the same transaction it is created (UnsafeNewEnumValueUsageError). The
'modified' enum value is added by this migration; the order_status_rank()
function (which references it) lives in 0007.

Splitting at this boundary keeps each migration transactional-DDL safe
and isolates the enum extension as a near-zero-risk change.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_status_enum ADD VALUE 'modified' AFTER 'submitted';")


def downgrade() -> None:
    # Postgres doesn't support DROP VALUE on an enum; downgrade is a no-op.
    # The 'modified' value lingers in the enum but nothing references it
    # after 0007 is downgraded. Acceptable since downgrade is dev-only.
    pass
