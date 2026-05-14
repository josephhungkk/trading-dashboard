"""Fix clean_tables guard in test fixtures — not a DB-level change.

Revision ID: 0048_fix_guard_delete_trigger
Down Revision: 0047_phase12_options
Create Date: 2026-05-14

Root-cause fix for repeated prod app_secrets wipes:

The test fixtures in test_admin_api.py and test_config_service.py guarded
against prod wipes with:

    if "10.10.0.2" in db_url and not _IN_DOCKER:
        pytest.skip(...)

The `not _IN_DOCKER` clause meant the guard was SKIPPED when pytest ran inside
the backend Docker container, even though that container's DATABASE_URL still
points at the prod NUC DB (10.10.0.2). The fix (in those test files) replaces
the check with:

    if "test_postgres" not in db_url and ":5433" not in db_url:
        pytest.skip(...)

This migration is a no-op at the DB level — it documents the schema version
bump and resets the alembic head pointer. The 0046 trigger (blocks unfiltered
multi-namespace deletes) is unchanged and sufficient now that the fixture guard
is correct.
"""

from __future__ import annotations

from alembic import op  # noqa: F401 — keep for alembic machinery


revision = "0048_fix_guard_delete_trigger"
down_revision = "0047_phase12_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No DB change needed — fix is in test fixture guard logic.
    pass


def downgrade() -> None:
    pass
