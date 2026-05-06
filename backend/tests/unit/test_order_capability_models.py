from __future__ import annotations

import pytest

from app.models.order_capability import BrokerOrderCapability, OrderType, TimeInForce


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Pure unit tests; shadow the global DB migration fixture."""


def test_order_type_tablename():
    assert OrderType.__tablename__ == "order_types"


def test_time_in_force_tablename():
    assert TimeInForce.__tablename__ == "time_in_force"


def test_broker_order_capability_tablename():
    assert BrokerOrderCapability.__tablename__ == "broker_order_capability"


def test_broker_order_capability_pk_columns():
    pk_names = {c.name for c in BrokerOrderCapability.__table__.primary_key.columns}
    assert pk_names == {"broker_id", "order_type", "time_in_force"}


def test_order_type_columns_match_schema():
    cols = {c.name for c in OrderType.__table__.columns}
    assert cols == {"code", "label", "description", "sort_order", "created_at"}


def test_time_in_force_has_requires_expiry():
    cols = {c.name for c in TimeInForce.__table__.columns}
    assert "requires_expiry" in cols


def test_broker_order_capability_has_check_constraint():
    constraint_names = {
        getattr(c, "name", None)
        for c in BrokerOrderCapability.__table__.constraints
        if hasattr(c, "name")
    }
    assert "broker_order_capability_notes_printable_ascii" in constraint_names
