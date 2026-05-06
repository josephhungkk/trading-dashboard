from decimal import Decimal

import pytest

from sidecar_schwab.normalize import (
    FillEvent,
    NormalizedOrder,
    StatusMapping,
    schwab_status_to_wire,
    schwab_to_wire_order,
    to_schwab_order_payload,
)


@pytest.mark.parametrize(
    ("schwab_status", "expected_wire", "expected_rank", "expected_terminal"),
    [
        ("AWAITING_PARENT_ORDER", "pending_submit", 0, False),
        ("PENDING_ACTIVATION", "pending_submit", 0, False),
        ("QUEUED", "submitted", 1, False),
        ("WORKING", "submitted", 1, False),
        ("PENDING_CANCEL", "cancel_requested", 2, False),
        ("PENDING_REPLACE", "modify_requested", 2, False),
        ("FILLED", "filled", 4, True),
        ("CANCELED", "cancelled", 4, True),
        ("REPLACED", "cancelled", 4, True),
        ("REJECTED", "rejected", 4, True),
        ("EXPIRED", "expired", 4, True),
    ],
)
def test_status_mapping(
    schwab_status: str,
    expected_wire: str,
    expected_rank: int,
    expected_terminal: bool,
) -> None:
    m = schwab_status_to_wire(schwab_status)
    assert m.wire_status == expected_wire
    assert m.rank == expected_rank
    assert m.terminal == expected_terminal


def test_replaced_status_emits_replaced_kind() -> None:
    m = schwab_status_to_wire("REPLACED")
    assert m.kind == "replaced"


def test_unknown_schwab_status_raises_warning() -> None:
    with pytest.warns(UserWarning, match="unknown schwab status"):
        m = schwab_status_to_wire("BOGUS_STATUS")
    assert m.wire_status == "submitted"


def test_executionleg_extracted_as_fill_event() -> None:
    order = {
        "orderId": 123,
        "status": "FILLED",
        "orderActivityCollection": [
            {
                "executionType": "FILL",
                "executionLegs": [
                    {
                        "legId": 1,
                        "price": 42.5,
                        "quantity": 10,
                        "time": "2026-05-06T12:00:00Z",
                    },
                ],
            }
        ],
    }
    result = schwab_to_wire_order(order, client_order_id="c-001")
    assert isinstance(result, NormalizedOrder)
    assert isinstance(result.status_mapping, StatusMapping)
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert isinstance(fill, FillEvent)
    assert fill.exec_id == "1"
    assert fill.price == Decimal("42.5")
    assert fill.avg_fill_price_inferred is False


def test_avg_fill_price_inferred_when_leg_price_null() -> None:
    order = {
        "orderId": 456,
        "status": "FILLED",
        "quantity": 100,
        "marketValue": 4250,
        "orderActivityCollection": [
            {
                "executionType": "FILL",
                "executionLegs": [
                    {
                        "legId": 2,
                        "price": None,
                        "quantity": 100,
                        "time": "2026-05-06T13:00:00Z",
                    },
                ],
            }
        ],
    }
    result = schwab_to_wire_order(order, client_order_id="c-002")
    assert len(result.fills) == 1
    assert result.fills[0].avg_fill_price_inferred is True
    assert result.fills[0].price == Decimal("42.5")


def test_to_schwab_order_payload_market_buy() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="100",
        symbol="AAPL",
    )

    assert payload == {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": "100",
                "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            }
        ],
    }


def test_to_schwab_order_payload_limit_includes_price() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty="100",
        symbol="AAPL",
        limit_price="50.25",
    )

    assert payload["price"] == "50.25"


def test_to_schwab_order_payload_gtc_maps_to_good_till_cancel() -> None:
    payload = to_schwab_order_payload(
        side="BUY",
        order_type="MARKET",
        tif="GTC",
        qty="100",
        symbol="AAPL",
    )

    assert payload["duration"] == "GOOD_TILL_CANCEL"
