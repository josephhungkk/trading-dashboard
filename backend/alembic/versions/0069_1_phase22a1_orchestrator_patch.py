"""Phase 22a.1 — sector ingestion + per_sector limits + veto window

Revision ID: 0069_1
Down Revision: 0069
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0069_1"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # instruments: sector classification
    op.add_column("instruments", sa.Column("sector", sa.Text(), nullable=True))
    op.add_column("instruments", sa.Column("sub_sector", sa.Text(), nullable=True))
    op.create_index(
        "instruments_sector_idx", "instruments", ["sector"],
        postgresql_where=sa.text("sector IS NOT NULL"),
    )

    # portfolio_exposure_limits: add per_sector type + sector column
    op.drop_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        type_="check",
    )
    op.create_check_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        "limit_type IN ('total_notional', 'per_instrument', 'per_sector')",
    )
    op.add_column(
        "portfolio_exposure_limits",
        sa.Column("sector", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_portfolio_exposure_sector",
        "portfolio_exposure_limits",
        ["account_id", "sector"],
        unique=True,
        postgresql_where=sa.text("limit_type = 'per_sector'"),
    )

    # shadow_promotion_events: veto window columns + extended status vocab
    op.add_column(
        "shadow_promotion_events",
        sa.Column("veto_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "shadow_promotion_events",
        sa.Column(
            "veto_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # CRIT-1: drop old check constraint (name is predictable: {table}_{column}_check)
    op.drop_constraint(
        "shadow_promotion_events_status_check",
        "shadow_promotion_events",
        type_="check",
    )
    op.create_check_constraint(
        "shadow_promotion_events_status_check_v2",
        "shadow_promotion_events",
        "status IN ('success','reverted','promote_pending','vetoed')",
    )
    # MED-5: veto_expires_at must be set iff status = 'promote_pending'
    op.create_check_constraint(
        "shadow_promotion_events_veto_expires_check",
        "shadow_promotion_events",
        "(status = 'promote_pending' AND veto_expires_at IS NOT NULL)"
        " OR (status <> 'promote_pending')",
    )
    op.create_index(
        "uq_shadow_promotion_pending",
        "shadow_promotion_events",
        ["live_bot_id", "shadow_bot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'promote_pending'"),
    )

    # Seed marginal_variance_enabled config (MED-6); value_json=NULL per exclusive check
    op.execute(text(
        "INSERT INTO app_config (namespace, key, value_type, value, value_json)"
        " VALUES ('orchestrator', 'marginal_variance_enabled', 'bool', 'true', NULL)"
        " ON CONFLICT (namespace, key) DO NOTHING"
    ))


def downgrade() -> None:
    op.execute(text(
        "DELETE FROM app_config"
        " WHERE namespace='orchestrator' AND key='marginal_variance_enabled'"
    ))
    op.drop_index("uq_shadow_promotion_pending", table_name="shadow_promotion_events")
    op.drop_constraint(
        "shadow_promotion_events_veto_expires_check",
        "shadow_promotion_events",
        type_="check",
    )
    op.drop_constraint(
        "shadow_promotion_events_status_check_v2",
        "shadow_promotion_events",
        type_="check",
    )
    op.create_check_constraint(
        "shadow_promotion_events_status_check",
        "shadow_promotion_events",
        "status IN ('success','reverted')",
    )
    op.drop_column("shadow_promotion_events", "veto_token")
    op.drop_column("shadow_promotion_events", "veto_expires_at")
    op.drop_index("uq_portfolio_exposure_sector", table_name="portfolio_exposure_limits")
    op.drop_column("portfolio_exposure_limits", "sector")
    op.drop_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        type_="check",
    )
    op.create_check_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        "limit_type IN ('total_notional', 'per_instrument')",
    )
    op.drop_index("instruments_sector_idx", table_name="instruments")
    op.drop_column("instruments", "sub_sector")
    op.drop_column("instruments", "sector")
