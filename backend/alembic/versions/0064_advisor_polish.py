"""Phase 21a.1: advisor override columns, SHADOW mode CHECK, CONCURRENTLY index."""

from alembic import op
import sqlalchemy as sa

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add 4 columns to bot_advisor_decisions
    op.add_column(
        "bot_advisor_decisions",
        sa.Column("overridden_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "bot_advisor_decisions",
        sa.Column("override_action", sa.Text(), nullable=True),
    )
    op.add_column(
        "bot_advisor_decisions",
        sa.Column("override_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "bot_advisor_decisions",
        sa.Column("overridden_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Add CHECK constraint for override_action
    op.execute(
        "ALTER TABLE bot_advisor_decisions ADD CONSTRAINT advisor_override_action_check"
        " CHECK (override_action IN ('approve', 'veto'))"
    )

    # 2. Create partial index CONCURRENTLY (requires autocommit)
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY bot_advisor_decisions_overridden_at_idx "
            "ON bot_advisor_decisions (overridden_at) WHERE overridden_at IS NOT NULL"
        )

    # 3. Pre-flight assertion: ensure no unknown modes exist before widening constraint
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM bots
                WHERE advisor_config IS NOT NULL
                  AND advisor_config->>'mode' NOT IN ('OFF', 'OBSERVE', 'VETO', 'SHADOW')
            ) THEN
                RAISE EXCEPTION 'bots.advisor_config has unknown mode values';
            END IF;
        END
        $$;
        """
    )

    # 4. Widen CHECK constraint on bots.advisor_config
    op.execute('ALTER TABLE bots DROP CONSTRAINT advisor_config_mode_check')
    op.execute(
        "ALTER TABLE bots ADD CONSTRAINT advisor_config_mode_check"
        " CHECK (advisor_config ? 'mode' AND advisor_config->>'mode'"
        " IN ('OFF', 'OBSERVE', 'VETO', 'SHADOW'))"
    )


def downgrade() -> None:
    # 1. Pre-flight: Update SHADOW modes to OBSERVE before narrowing CHECK
    op.execute(
        """
        UPDATE bots
        SET advisor_config = jsonb_set(advisor_config, '{mode}', '"OBSERVE"')
        WHERE advisor_config->>'mode' = 'SHADOW';
        """
    )

    # 2. Re-create CHECK with only OFF, OBSERVE, VETO
    op.execute('ALTER TABLE bots DROP CONSTRAINT advisor_config_mode_check')
    op.execute(
        "ALTER TABLE bots ADD CONSTRAINT advisor_config_mode_check"
        " CHECK (advisor_config ? 'mode' AND advisor_config->>'mode'"
        " IN ('OFF', 'OBSERVE', 'VETO'))"
    )

    # 3. Drop CONCURRENTLY index
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS bot_advisor_decisions_overridden_at_idx")

    # 4. Drop override CHECK and 4 columns in order
    op.execute("ALTER TABLE bot_advisor_decisions DROP CONSTRAINT advisor_override_action_check")
    op.drop_column("bot_advisor_decisions", "overridden_at")
    op.drop_column("bot_advisor_decisions", "override_reason")
    op.drop_column("bot_advisor_decisions", "override_action")
    op.drop_column("bot_advisor_decisions", "overridden_by")
