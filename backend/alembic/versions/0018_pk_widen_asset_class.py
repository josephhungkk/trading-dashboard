"""Phase 8c T-0.1 -- widen capability PKs with asset_class.

ATOMIC single-transaction migration. Locks are acquired first before reshaping
the primary keys. Downgrade drops asset_class and deletes non-STOCK rows first;
data for non-STOCK capability rows is not recoverable after downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_pk_widen_asset_class"
down_revision = "0017a_oco_links_partial_index"
branch_labels = None
depends_on = None


ORDER_TYPES = (
    "MARKET",
    "LIMIT",
    "STOP",
    "STOP_LIMIT",
    "TRAIL",
    "TRAIL_LIMIT",
    "MOC",
    "MOO",
    "LOC",
    "LOO",
)
TIME_IN_FORCE = ("DAY", "GTC", "IOC", "FOK", "GTD")
SESSION_BOUND_ORDER_TYPES = frozenset(("MOC", "MOO", "LOC", "LOO"))
ORDER_CAPABILITY_BROKERS = ("alpaca", "schwab", "ibkr", "futu")
NOTIFY_BROKERS = ("alpaca", "schwab", "ibkr", "futu")


def _notify_order_capability_invalidation(bind: sa.engine.Connection) -> None:
    for broker_id in NOTIFY_BROKERS:
        bind.execute(
            sa.text("SELECT pg_notify('app_config:invalidate:order_capabilities', :b)"),
            {"b": broker_id},
        )


def upgrade() -> None:
    op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE broker_features IN ACCESS EXCLUSIVE MODE")

    op.add_column(
        "broker_order_capability",
        sa.Column("asset_class", sa.String(16), nullable=True),
    )
    op.execute(
        "UPDATE broker_order_capability "
        "SET asset_class = 'STOCK' "
        "WHERE asset_class IS NULL"
    )
    op.alter_column("broker_order_capability", "asset_class", nullable=False)
    op.execute(
        "ALTER TABLE broker_order_capability "
        "ADD CONSTRAINT broker_order_capability_asset_class_check "
        "CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'))"
    )
    op.execute(
        "ALTER TABLE broker_order_capability "
        "DROP CONSTRAINT broker_order_capability_pkey"
    )
    op.execute(
        "ALTER TABLE broker_order_capability "
        "ADD PRIMARY KEY (broker_id, asset_class, order_type, time_in_force)"
    )

    op.add_column(
        "broker_features",
        sa.Column(
            "asset_class",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'STOCK'"),
        ),
    )
    op.execute(
        "ALTER TABLE broker_features "
        "ADD CONSTRAINT broker_features_asset_class_check "
        "CHECK (asset_class IN ('STOCK','CRYPTO','OPTION','FUTURE','FOREX','BOND'))"
    )
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_pkey")
    op.execute(
        "ALTER TABLE broker_features "
        "ADD PRIMARY KEY (broker_id, asset_class, feature)"
    )
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_feature_check")
    op.execute(
        "ALTER TABLE broker_features "
        "ADD CONSTRAINT broker_features_feature_check "
        "CHECK (feature IN ("
        "'modify','bracket','oco','gtd_max_days',"
        "'session_cutoff_minutes','notional_orders'"
        "))"
    )

    bind = op.get_bind()
    for broker_id in ORDER_CAPABILITY_BROKERS:
        for order_type in ORDER_TYPES:
            for time_in_force in TIME_IN_FORCE:
                is_supported = (
                    broker_id == "alpaca"
                    and order_type in ("MARKET", "LIMIT")
                    and time_in_force in ("GTC", "IOC", "FOK")
                    and order_type not in SESSION_BOUND_ORDER_TYPES
                )
                notes = ""
                if broker_id != "alpaca":
                    notes = "Crypto not supported - placeholder for Phase 15+"
                elif order_type in SESSION_BOUND_ORDER_TYPES:
                    notes = "Crypto 24/7 - session-bound order type not applicable"
                elif not is_supported:
                    notes = "Crypto capability pending empirical validation"
                bind.execute(
                    sa.text(
                        "INSERT INTO broker_order_capability "
                        "(broker_id, asset_class, order_type, time_in_force, "
                        "is_supported, notes, updated_at) "
                        "VALUES (:b, 'CRYPTO', :ot, :tif, :s, :n, NOW()) "
                        "ON CONFLICT "
                        "(broker_id, asset_class, order_type, time_in_force) "
                        "DO NOTHING"
                    ),
                    {
                        "b": broker_id,
                        "ot": order_type,
                        "tif": time_in_force,
                        "s": is_supported,
                        "n": notes,
                    },
                )

    for broker_id, asset_class, is_supported in (
        ("alpaca", "STOCK", True),
        ("alpaca", "CRYPTO", True),
        ("schwab", "STOCK", False),
        ("ibkr", "STOCK", False),
        ("futu", "STOCK", False),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO broker_features "
                "(broker_id, asset_class, feature, is_supported, notes, updated_at) "
                "VALUES (:b, :ac, 'notional_orders', :s, :n, NOW()) "
                "ON CONFLICT (broker_id, asset_class, feature) DO NOTHING"
            ),
            {
                "b": broker_id,
                "ac": asset_class,
                "s": is_supported,
                "n": "" if is_supported else "Notional orders not supported",
            },
        )

    for asset_class, is_supported in (("STOCK", True), ("CRYPTO", False)):
        bind.execute(
            sa.text(
                "INSERT INTO broker_features "
                "(broker_id, asset_class, feature, is_supported, notes, updated_at) "
                "VALUES ('alpaca', :ac, 'bracket', :s, :n, NOW()) "
                "ON CONFLICT (broker_id, asset_class, feature) DO NOTHING"
            ),
            {
                "ac": asset_class,
                "s": is_supported,
                "n": (
                    "Phase 8c T-0.1 stock bracket seed"
                    if is_supported
                    else "Crypto bracket pending empirical validation"
                ),
            },
        )

    _notify_order_capability_invalidation(bind)


def downgrade() -> None:
    op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE broker_features IN ACCESS EXCLUSIVE MODE")

    op.execute("DELETE FROM broker_order_capability WHERE asset_class <> 'STOCK'")
    op.execute(
        "DELETE FROM broker_features "
        "WHERE asset_class <> 'STOCK' OR feature = 'notional_orders'"
    )
    op.execute(
        "DELETE FROM broker_features "
        "WHERE broker_id = 'alpaca' AND asset_class = 'STOCK' AND feature = 'bracket'"
        " AND notes LIKE '%Phase 8c T-0.1%'"
    )

    op.execute(
        "ALTER TABLE broker_order_capability "
        "DROP CONSTRAINT broker_order_capability_pkey"
    )
    op.execute(
        "ALTER TABLE broker_order_capability "
        "ADD PRIMARY KEY (broker_id, order_type, time_in_force)"
    )
    op.execute(
        "ALTER TABLE broker_order_capability "
        "DROP CONSTRAINT broker_order_capability_asset_class_check"
    )
    op.drop_column("broker_order_capability", "asset_class")

    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_pkey")
    op.execute(
        "ALTER TABLE broker_features ADD PRIMARY KEY (broker_id, feature)"
    )
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_asset_class_check")
    op.execute("ALTER TABLE broker_features DROP CONSTRAINT broker_features_feature_check")
    op.execute(
        "ALTER TABLE broker_features "
        "ADD CONSTRAINT broker_features_feature_check "
        "CHECK (feature IN ("
        "'modify','bracket','oco','gtd_max_days','session_cutoff_minutes'"
        "))"
    )
    op.drop_column("broker_features", "asset_class")

    _notify_order_capability_invalidation(op.get_bind())
