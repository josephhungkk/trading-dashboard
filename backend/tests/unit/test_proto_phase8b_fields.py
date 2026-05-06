from __future__ import annotations

from app._generated.broker.v1 import broker_pb2


def test_place_order_request_has_phase8b_fields() -> None:
    req = broker_pb2.PlaceOrderRequest(
        account_number="ACCT-1",
        client_order_id="cli-1",
        conid="265598",
        side="BUY",
        order_type="TRAIL",
        tif="DAY",
        qty="1",
        trail_offset="0.10",
        trail_offset_type="AMOUNT",
    )
    assert req.trail_offset == "0.10"
    assert req.trail_offset_type == "AMOUNT"
    assert req.trail_limit_offset == ""
    assert req.expiry_date == ""


def test_modify_order_request_has_phase8b_fields() -> None:
    req = broker_pb2.ModifyOrderRequest(
        broker_order_id="12345",
        account_number="ACCT-1",
        client_order_id="cli-2",
        side="BUY",
        order_type="TRAIL_LIMIT",
        tif="GTD",
        qty="2",
        trail_offset="0.20",
        trail_offset_type="PERCENT",
        trail_limit_offset="0.05",
        expiry_date="2026-05-09",
    )
    assert req.trail_offset == "0.20"
    assert req.trail_offset_type == "PERCENT"
    assert req.trail_limit_offset == "0.05"
    assert req.expiry_date == "2026-05-09"


def test_order_message_has_phase8b_fields() -> None:
    order = broker_pb2.Order(
        order_id="OID-1",
        side="BUY",
        order_type="TRAIL",
        time_in_force="GTD",
        quantity="100",
        trail_offset="0.50",
        trail_offset_type="AMOUNT",
        expiry_date="2026-12-31",
    )
    assert order.trail_offset == "0.50"
    assert order.expiry_date == "2026-12-31"
