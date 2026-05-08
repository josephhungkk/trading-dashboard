"""Phase 9.5 retro: lower order_status_rank('modified') to equal 'submitted'.

When IBKR rejects modify, it returns the order to 'Submitted' status. Original
ranking made 'modified'=2 > 'submitted'=1, filtering the rejection event and
stranding the order with stale params.

Revision ID: 0032_phase9_5_order_status_rank_modified
Revises: 0031a_phase9_5_last_seen_via_index
"""

from alembic import op

revision = "0032_phase9_5_order_status_rank_modified"
down_revision = "0031a_phase9_5_last_seen_via_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION order_status_rank(s order_status_enum)
        RETURNS INT IMMUTABLE LANGUAGE SQL AS $$
            SELECT CASE s
                WHEN 'pending_submit' THEN 0
                WHEN 'submitted' THEN 1
                WHEN 'inactive' THEN 1
                WHEN 'modified' THEN 1
                WHEN 'partial' THEN 3
                WHEN 'filled' THEN 4
                WHEN 'cancelled' THEN 5
                WHEN 'rejected' THEN 5
                WHEN 'expired' THEN 5
                ELSE -1
            END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION order_status_rank(s order_status_enum)
        RETURNS INT IMMUTABLE LANGUAGE SQL AS $$
            SELECT CASE s
                WHEN 'pending_submit' THEN 0
                WHEN 'submitted' THEN 1
                WHEN 'inactive' THEN 1
                WHEN 'modified' THEN 2
                WHEN 'partial' THEN 3
                WHEN 'filled' THEN 4
                WHEN 'cancelled' THEN 5
                WHEN 'rejected' THEN 5
                WHEN 'expired' THEN 5
                ELSE -1
            END
        $$;
        """
    )
