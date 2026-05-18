"""Phase 16a: BOND asset class, bonds_accrued_interest table."""
from __future__ import annotations
from alembic import op

revision = "0053_phase16a_bonds"
down_revision = "0052_phase15b_crypto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'BOND'")
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'bond_max_notional_per_trade', 1000000, true, 'migration-0053'),
               ('global', NULL, 'bond_max_concentration_pct', 25, true, 'migration-0053')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS bonds_accrued_interest (
            id             BIGSERIAL PRIMARY KEY,
            instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            account_id     UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            accrued        NUMERIC(20,8) NOT NULL,
            as_of          DATE NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (instrument_id, account_id, as_of)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS bonds_accrued_interest_instrument_idx
            ON bonds_accrued_interest(instrument_id, as_of DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bonds_accrued_interest")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('bond_max_notional_per_trade', 'bond_max_concentration_pct')"
    )
