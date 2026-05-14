"""Phase 12: OPTION asset class, position_effect, tax_treatment, option tables."""

from __future__ import annotations

from alembic import op

revision = "0047_phase12_options"
down_revision = "0046_protect_app_config_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'OPTION'")

    op.execute(
        """
        UPDATE instruments
        SET meta = jsonb_set(meta, '{asset_class}', to_jsonb(asset_class::text))
        WHERE meta != '{}' AND meta->>'asset_class' IS NULL
        """
    )

    op.execute(
        """
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS position_effect TEXT
            CHECK (position_effect IS NULL OR position_effect IN ('OPEN', 'CLOSE'))
        """
    )
    op.execute(
        """
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS tax_treatment TEXT
            CHECK (tax_treatment IS NULL OR tax_treatment IN
              ('EQUITY','OPTION_PREMIUM','OPTION_EXERCISE','OPTION_ASSIGNMENT','OPTION_EXPIRY'))
        """
    )
    op.execute(
        """
        ALTER TABLE fills
        ADD COLUMN IF NOT EXISTS tax_treatment TEXT
            CHECK (tax_treatment IS NULL OR tax_treatment IN
              ('EQUITY','OPTION_PREMIUM','OPTION_EXERCISE','OPTION_ASSIGNMENT','OPTION_EXPIRY'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_greeks (
            instrument_id  BIGINT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
            delta          NUMERIC(12, 6),
            gamma          NUMERIC(12, 6),
            theta          NUMERIC(12, 6),
            vega           NUMERIC(12, 6),
            rho            NUMERIC(12, 6),
            iv             NUMERIC(12, 6),
            iv_rank        NUMERIC(5, 2),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS option_greeks_updated_at_idx
            ON option_greeks (updated_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exercise_elections (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key  UUID NOT NULL UNIQUE,
            jwt_subject      TEXT NOT NULL,
            account_id       UUID NOT NULL REFERENCES broker_accounts(id),
            instrument_id    BIGINT NOT NULL REFERENCES instruments(id),
            action           TEXT NOT NULL CHECK (
                action IN ('EXERCISE', 'DO_NOT_EXERCISE', 'LAPSE')
            ),
            qty              NUMERIC(20, 8) NOT NULL,
            status           TEXT NOT NULL DEFAULT 'submitted'
                               CHECK (status IN ('submitted', 'confirmed', 'failed')),
            broker_ref       TEXT,
            error_reason     TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS exercise_elections_one_per_day
            ON exercise_elections (
                account_id,
                instrument_id,
                ((created_at AT TIME ZONE 'UTC')::date)
            )
            WHERE status != 'failed'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS exercise_elections_one_per_day")
    op.execute("DROP TABLE IF EXISTS exercise_elections")
    op.execute("DROP TABLE IF EXISTS option_greeks")
    op.execute("ALTER TABLE fills DROP COLUMN IF EXISTS tax_treatment")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tax_treatment")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS position_effect")
