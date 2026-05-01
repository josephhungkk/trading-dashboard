import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.normalize import (
    map_asset_type,
    map_order_type,
    map_status,
    map_tif,
    normalize_account,
    normalize_order,
    normalize_position,
    normalize_summary,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AWAITING_PARENT_ORDER", broker_pb2.OrderStatus.PENDING),
        ("AWAITING_CONDITION", broker_pb2.OrderStatus.PENDING),
        ("AWAITING_STOP_CONDITION", broker_pb2.OrderStatus.PENDING),
        ("AWAITING_MANUAL_REVIEW", broker_pb2.OrderStatus.PENDING),
        ("ACCEPTED", broker_pb2.OrderStatus.SUBMITTED),
        ("AWAITING_UR_OUT", broker_pb2.OrderStatus.SUBMITTED),
        ("PENDING_ACTIVATION", broker_pb2.OrderStatus.SUBMITTED),
        ("QUEUED", broker_pb2.OrderStatus.SUBMITTED),
        ("WORKING", broker_pb2.OrderStatus.SUBMITTED),
        ("REJECTED", broker_pb2.OrderStatus.REJECTED),
        ("PENDING_CANCEL", broker_pb2.OrderStatus.PENDING_CANCEL),
        ("CANCELED", broker_pb2.OrderStatus.CANCELED),
        ("PENDING_REPLACE", broker_pb2.OrderStatus.SUBMITTED),
        ("REPLACED", broker_pb2.OrderStatus.SUBMITTED),
        ("FILLED", broker_pb2.OrderStatus.FILLED),
        ("EXPIRED", broker_pb2.OrderStatus.EXPIRED),
        ("NEW", broker_pb2.OrderStatus.SUBMITTED),
        ("AWAITING_RELEASE_TIME", broker_pb2.OrderStatus.PENDING),
        ("WHO_KNOWS", broker_pb2.OrderStatus.SUBMITTED),
    ],
)
def test_status_mapping(raw, expected):
    assert map_status(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EQUITY", broker_pb2.AssetClass.STOCK),
        ("ETF", broker_pb2.AssetClass.STOCK),
        ("INDEX", broker_pb2.AssetClass.STOCK),
        ("OPTION", broker_pb2.AssetClass.OPTION),
        ("FUTURES", broker_pb2.AssetClass.FUTURE),
        ("FUTURE_OPTION", broker_pb2.AssetClass.FUTURE),
        ("FOREX", broker_pb2.AssetClass.FX),
        ("FIXED_INCOME", broker_pb2.AssetClass.BOND),
        ("MUTUAL_FUND", broker_pb2.AssetClass.STOCK),
        ("CASH_EQUIVALENT", broker_pb2.AssetClass.STOCK),
    ],
)
def test_asset_type_mapping(raw, expected):
    assert map_asset_type(raw) == expected


def test_normalize_account_is_live_with_usd_currency_base():
    raw = {"accountNumber": "123456789", "currency": "USD"}
    acct = normalize_account(raw)
    assert acct.account_id == "123456789"
    assert acct.mode == broker_pb2.TradingMode.LIVE
    assert acct.currency_base == "USD"


def test_normalize_summary_uses_money_proto():
    raw = {
        "securitiesAccount": {
            "currentBalances": {
                "liquidationValue": 50000.0,
                "cashBalance": 10000.0,
                "buyingPower": 20000.0,
            }
        }
    }
    summary = normalize_summary(raw)
    assert summary.liquidation_value.value == "50000.0"
    assert summary.cash.value == "10000.0"
    assert summary.buying_power.value == "20000.0"


def test_normalize_position_uses_money_for_all_decimal_fields():
    raw = {
        "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
        "longQuantity": 10.0,
        "shortQuantity": 0.0,
        "averagePrice": 150.0,
        "marketValue": 1600.0,
        "unrealizedPnL": 100.0,
    }
    pos = normalize_position(raw)
    assert pos.contract.symbol == "AAPL"
    assert pos.contract.asset_class == broker_pb2.AssetClass.STOCK
    assert pos.average_cost.value == "150.0"
    assert pos.market_value.value == "1600.0"
    assert pos.unrealized_pnl.value == "100.0"
    assert pos.quantity == "10.0"


def test_normalize_order_extracts_avg_fill_from_order_activity_collection():
    raw = {
        "orderId": 99,
        "status": "FILLED",
        "orderType": "LIMIT",
        "duration": "DAY",
        "quantity": 10.0,
        "filledQuantity": 10.0,
        "price": 100.0,
        "orderLegCollection": [{"instrument": {"symbol": "TSLA", "assetType": "EQUITY"}}],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionLegs": [
                    {"quantity": 5.0, "price": 98.0},
                    {"quantity": 5.0, "price": 102.0},
                ],
            }
        ],
    }
    order = normalize_order(raw)
    assert order.order_id == "99"
    assert order.avg_fill_price.value == "100.0"
    assert order.quantity_filled == "10.0"
    assert order.avg_fill_price_inferred is False


def test_normalize_order_filled_without_order_activity_collection_marks_inferred():
    raw = {
        "orderId": 42,
        "status": "FILLED",
        "orderType": "MARKET",
        "duration": "DAY",
        "quantity": 5.0,
        "filledQuantity": 5.0,
        "price": 200.0,
        "orderLegCollection": [{"instrument": {"symbol": "MSFT", "assetType": "EQUITY"}}],
    }
    order = normalize_order(raw)
    assert order.avg_fill_price_inferred is True
    assert order.quantity_filled == "5.0"


def test_normalize_modified_status_maps_to_submitted_not_modified():
    assert map_status("PENDING_REPLACE") == broker_pb2.OrderStatus.SUBMITTED
    assert map_status("REPLACED") == broker_pb2.OrderStatus.SUBMITTED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MARKET", broker_pb2.OrderType.MARKET),
        ("LIMIT", broker_pb2.OrderType.LIMIT),
        ("STOP", broker_pb2.OrderType.STOP),
        ("STOP_LIMIT", broker_pb2.OrderType.STOP_LIMIT),
        ("TRAILING_STOP", broker_pb2.OrderType.TRAILING_STOP),
        ("TRAILING_STOP_LIMIT", broker_pb2.OrderType.TRAILING_STOP),
        ("MARKET_ON_CLOSE", broker_pb2.OrderType.MARKET),
        ("EXERCISE", broker_pb2.OrderType.MARKET),
        ("CABINET", broker_pb2.OrderType.LIMIT),
        ("NET_DEBIT", broker_pb2.OrderType.LIMIT),
        ("NET_CREDIT", broker_pb2.OrderType.LIMIT),
        ("NET_ZERO", broker_pb2.OrderType.LIMIT),
        ("WHO_KNOWS", broker_pb2.OrderType.MARKET),
    ],
)
def test_order_type_mapping(raw, expected):
    assert map_order_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DAY", broker_pb2.TimeInForce.DAY),
        ("GTC", broker_pb2.TimeInForce.GTC),
        ("FOK", broker_pb2.TimeInForce.FOK),
        ("IOC", broker_pb2.TimeInForce.IOC),
        ("END_OF_WEEK", broker_pb2.TimeInForce.GTC),
        ("END_OF_MONTH", broker_pb2.TimeInForce.GTC),
        ("NEXT_END_OF_MONTH", broker_pb2.TimeInForce.GTC),
        ("UNKNOWN", broker_pb2.TimeInForce.DAY),
        ("WHO_KNOWS", broker_pb2.TimeInForce.DAY),
    ],
)
def test_tif_mapping(raw, expected):
    assert map_tif(raw) == expected
