from __future__ import annotations

from app._generated.broker.v1 import broker_pb2


def test_place_order_request_has_cash_amount_field() -> None:
    field = broker_pb2.PlaceOrderRequest.DESCRIPTOR.fields_by_name["cash_amount"]
    assert field.number == 15


def test_cash_amount_default_empty_string() -> None:
    req = broker_pb2.PlaceOrderRequest()
    assert req.cash_amount == ""


def test_cash_amount_round_trip() -> None:
    req = broker_pb2.PlaceOrderRequest(cash_amount="100.00")
    data = req.SerializeToString()

    parsed = broker_pb2.PlaceOrderRequest()
    parsed.ParseFromString(data)

    assert parsed.cash_amount == "100.00"


def test_modify_order_request_has_cash_amount_field() -> None:
    field = broker_pb2.ModifyOrderRequest.DESCRIPTOR.fields_by_name["cash_amount"]
    assert field.number == 15


def test_order_has_cash_amount_field() -> None:
    field = broker_pb2.Order.DESCRIPTOR.fields_by_name["cash_amount"]
    assert field.number == 15
