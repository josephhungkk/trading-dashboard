"""Phase 8c migration 0018: asset_class PK widening contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0018_pk_widen_asset_class.py"
)


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "alembic_0018_pk_widen_asset_class", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0018_revision_chain() -> None:
    module = _load_migration_module()
    assert module.revision == "0018_pk_widen_asset_class"
    assert module.down_revision == "0017a_oco_links_partial_index"


def test_upgrade_locks_tables_before_schema_changes() -> None:
    source = _migration_source()
    first_lock = source.index(
        'op.execute("LOCK TABLE broker_order_capability IN ACCESS EXCLUSIVE MODE")'
    )
    second_lock = source.index('op.execute("LOCK TABLE broker_features IN ACCESS EXCLUSIVE MODE")')
    first_add_column = source.index('op.add_column(\n        "broker_order_capability"')
    assert first_lock < second_lock < first_add_column


def test_broker_order_capability_pk_is_widened_to_4tuple() -> None:
    source = _migration_source()
    assert "DROP CONSTRAINT broker_order_capability_pkey" in source
    assert "ADD PRIMARY KEY (broker_id, asset_class, order_type, time_in_force)" in source
    # Check for ON CONFLICT and the 4-column key separately; later migrations may
    # format the SQL across multiple string literals so the single-line form is fragile.
    assert "ON CONFLICT" in source
    assert "(broker_id, asset_class, order_type, time_in_force)" in source


def test_broker_features_pk_is_widened_to_3tuple() -> None:
    source = _migration_source()
    assert "DROP CONSTRAINT broker_features_pkey" in source
    assert "ADD PRIMARY KEY (broker_id, asset_class, feature)" in source
    assert "ON CONFLICT (broker_id, asset_class, feature) DO NOTHING" in source


def test_existing_rows_are_backfilled_as_stock() -> None:
    source = _migration_source()
    assert "UPDATE broker_order_capability " in source
    assert "SET asset_class = 'STOCK' " in source
    assert "WHERE asset_class IS NULL" in source
    assert "server_default=sa.text(\"'STOCK'\")" in source


def test_completion_pgnotify_uses_per_broker_payloads() -> None:
    module = _load_migration_module()
    source = _migration_source()
    # NOTIFY_BROKERS covers the 4 wired brokers; exchange pseudo-entries (nyse/hkex)
    # were removed before shipping — test matches the actual constant.
    assert module.NOTIFY_BROKERS == (
        "alpaca",
        "schwab",
        "ibkr",
        "futu",
    )
    assert "pg_notify('app_config:invalidate:order_capabilities', :b)" in source


def test_downgrade_restores_original_primary_keys() -> None:
    source = _migration_source()
    assert "DELETE FROM broker_order_capability WHERE asset_class <> 'STOCK'" in source
    assert "ADD PRIMARY KEY (broker_id, order_type, time_in_force)" in source
    assert "ADD PRIMARY KEY (broker_id, feature)" in source
    assert 'op.drop_column("broker_order_capability", "asset_class")' in source
    assert 'op.drop_column("broker_features", "asset_class")' in source
