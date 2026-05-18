"""Phase 16b: MUTUAL_FUND asset class, fund_nav_snapshots hypertable."""
from __future__ import annotations
from alembic import op

revision = "0054_phase16b_funds"
down_revision = "0053_phase16a_bonds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'MUTUAL_FUND'")
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'fund_max_notional_per_trade', 500000, true, 'migration-0054'),
               ('global', NULL, 'fund_max_concentration_pct', 25, true, 'migration-0054')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS fund_nav_snapshots (
            instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            nav            NUMERIC(20,8) NOT NULL,
            nav_date       DATE NOT NULL,
            source         TEXT NOT NULL DEFAULT 'ibkr'
                           CHECK (source IN ('ibkr', 'schwab')),
            captured_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("SELECT create_hypertable('fund_nav_snapshots', 'captured_at', if_not_exists => TRUE)")
    op.execute("SELECT add_retention_policy('fund_nav_snapshots', INTERVAL '2 years', if_not_exists => TRUE)")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS fund_nav_snapshots_instrument_date_source_idx
            ON fund_nav_snapshots (instrument_id, nav_date, source, captured_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fund_nav_snapshots")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('fund_max_notional_per_trade', 'fund_max_concentration_pct')"
    )
