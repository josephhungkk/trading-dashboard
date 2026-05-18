"""Phase 16c: CFD asset class, broker_accounts.country column."""
from __future__ import annotations
from alembic import op

revision = "0055_phase16c_cfd"
down_revision = "0054_phase16b_funds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_leverage'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'CFD'")
    op.execute("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS country TEXT")
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'cfd_max_notional_per_trade', 250000, true, 'migration-0055'),
               ('global', NULL, 'cfd_max_leverage', 20, true, 'migration-0055'),
               ('global', NULL, 'cfd_max_concentration_pct', 25, true, 'migration-0055')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE broker_accounts DROP COLUMN IF EXISTS country")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('cfd_max_notional_per_trade', 'cfd_max_leverage', 'cfd_max_concentration_pct')"
    )
