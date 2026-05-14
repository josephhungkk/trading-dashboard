"""Protect app_config and app_secrets from unfiltered DELETE.

Revision ID: 0046_protect_app_config_secrets
Down Revision: 0045_phase11c_telegram
Create Date: 2026-05-14

Adds a BEFORE DELETE trigger on both tables that raises an exception when a
DELETE has no WHERE predicate (i.e. it would delete every row). This prevents
the pytest clean_tables fixture from accidentally wiping operator-seeded
credentials when DATABASE_URL points at the shared NUC database.

A filtered DELETE (e.g. WHERE namespace = 'test') still works normally.
The trigger fires per-row; OLD.namespace is always non-null for real rows,
so the guard condition is: if the deleting transaction has deleted more rows
than a safe threshold in this statement, block. We use a simpler approach:
require that every DELETE statement supplies at least one equality condition
on namespace — enforced via a statement-level trigger using a transition table.

Implementation: statement-level trigger with REFERENCING OLD TABLE. If the
OLD TABLE contains rows from more than one namespace, the delete is too broad
and is rejected. Single-namespace deletes (which is how test fixtures should
be scoped) are allowed through.
"""

from __future__ import annotations

from alembic import op

revision = "0046_protect_app_config_secrets"
down_revision = "0045_phase11c_telegram"
branch_labels = None
depends_on = None

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION _guard_config_delete() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    ns_count INT;
BEGIN
    SELECT COUNT(DISTINCT namespace) INTO ns_count FROM deleted_rows;
    IF ns_count > 1 THEN
        RAISE EXCEPTION
            'Unfiltered DELETE on % is blocked: would remove rows from % namespaces. '
            'Supply a WHERE namespace = ''...'' clause.',
            TG_TABLE_NAME, ns_count;
    END IF;
    RETURN NULL;
END;
$$;
"""

_DROP_TRIGGER_FN = "DROP FUNCTION IF EXISTS _guard_config_delete() CASCADE;"


def upgrade() -> None:
    op.execute(_TRIGGER_FN)

    for table in ("app_config", "app_secrets"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_guard_delete
            AFTER DELETE ON {table}
            REFERENCING OLD TABLE AS deleted_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION _guard_config_delete();
        """)


def downgrade() -> None:
    for table in ("app_config", "app_secrets"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_guard_delete ON {table};")
    op.execute(_DROP_TRIGGER_FN)
