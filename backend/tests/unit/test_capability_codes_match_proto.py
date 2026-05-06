"""Phase 8a invariant: DB order_types.code + time_in_force.code ⊆ proto enum.

This test must run BEFORE Alembic 0011 lands to confirm the proto enum
already covers the universe Task A3 will seed.
"""

from __future__ import annotations

import typing as t

from app._generated.broker.v1 import broker_pb2
from app.brokers import base


def _proto_enum_values(enum_descriptor) -> set[str]:
    """Strip ORDER_TYPE_ / TIF_ prefix that protoc emits."""
    out: set[str] = set()
    for v in enum_descriptor.values:
        name = v.name
        if name.startswith("ORDER_TYPE_"):
            out.add(name[len("ORDER_TYPE_") :])
        elif name.startswith("TIF_"):
            out.add(name[len("TIF_") :])
    return out


def test_python_literal_order_type_subset_of_proto() -> None:
    proto_codes = _proto_enum_values(broker_pb2.OrderType.DESCRIPTOR)
    literal_codes = set(t.get_args(base.OrderType))
    missing = literal_codes - proto_codes
    assert not missing, f"Python Literal has codes not in proto: {missing}"


def test_python_literal_tif_subset_of_proto() -> None:
    proto_codes = _proto_enum_values(broker_pb2.TimeInForce.DESCRIPTOR)
    literal_codes = set(t.get_args(base.TimeInForce))
    missing = literal_codes - proto_codes
    assert not missing, f"Python Literal has codes not in proto: {missing}"


def test_proto_includes_phase8_universe() -> None:
    """Defensive: confirm the proto enum already extends to Phase 8 universe."""
    proto_order = _proto_enum_values(broker_pb2.OrderType.DESCRIPTOR)
    proto_tif = _proto_enum_values(broker_pb2.TimeInForce.DESCRIPTOR)
    expected_order = {
        "UNSPECIFIED",
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
    }
    expected_tif = {"UNSPECIFIED", "DAY", "GTC", "IOC", "FOK", "GTD"}
    assert proto_order >= expected_order, f"proto missing types: {expected_order - proto_order}"
    assert proto_tif >= expected_tif, f"proto missing TIFs: {expected_tif - proto_tif}"
