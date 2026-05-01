"""Normalize Schwab REST JSON payloads into broker protos."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.metrics import SCHWAB_NORMALIZE_UNKNOWN_TOTAL


def _install_proto_compat_aliases() -> None:
    """Expose Phase 7a compatibility names without editing generated code."""
    if not hasattr(broker_pb2, "AccountSummary"):
        broker_pb2.AccountSummary = broker_pb2.Summary

    aliases = (
        (broker_pb2.Account, "account_id", "account_number"),
        (broker_pb2.Summary, "liquidation_value", "net_liquidation"),
        (broker_pb2.Summary, "cash", "total_cash"),
        (broker_pb2.Position, "average_cost", "avg_cost"),
    )
    for message_type, alias, field_name in aliases:
        if not hasattr(message_type, alias):
            setattr(
                message_type,
                alias,
                property(lambda self, field_name=field_name: getattr(self, field_name)),
            )

    enum_aliases = (
        (broker_pb2.AssetClass, "UNKNOWN_ASSET_CLASS", broker_pb2.AssetClass.ASSET_UNSPECIFIED),
        (broker_pb2.AssetClass, "FX", broker_pb2.AssetClass.FOREX),
        (broker_pb2.OrderStatus, "CANCELED", broker_pb2.OrderStatus.CANCELLED),
        (broker_pb2.OrderStatus, "PENDING_CANCEL", 8),
        (broker_pb2.OrderStatus, "EXPIRED", 9),
        (broker_pb2.OrderType, "TRAILING_STOP", broker_pb2.OrderType.STOP),
    )
    for enum_type, alias, value in enum_aliases:
        if not hasattr(enum_type, alias):
            setattr(enum_type, alias, value)


_install_proto_compat_aliases()


def _money(value: str | float | int | Decimal, currency: str = "USD") -> broker_pb2.Money:
    return broker_pb2.Money(value=str(value), currency=currency)


_STATUS_MAP = {
    "AWAITING_PARENT_ORDER": broker_pb2.OrderStatus.PENDING,
    "AWAITING_CONDITION": broker_pb2.OrderStatus.PENDING,
    "AWAITING_STOP_CONDITION": broker_pb2.OrderStatus.PENDING,
    "AWAITING_MANUAL_REVIEW": broker_pb2.OrderStatus.PENDING,
    "ACCEPTED": broker_pb2.OrderStatus.SUBMITTED,
    "AWAITING_UR_OUT": broker_pb2.OrderStatus.SUBMITTED,
    "PENDING_ACTIVATION": broker_pb2.OrderStatus.SUBMITTED,
    "QUEUED": broker_pb2.OrderStatus.SUBMITTED,
    "WORKING": broker_pb2.OrderStatus.SUBMITTED,
    "REJECTED": broker_pb2.OrderStatus.REJECTED,
    "PENDING_CANCEL": broker_pb2.OrderStatus.PENDING_CANCEL,
    "CANCELED": broker_pb2.OrderStatus.CANCELED,
    "PENDING_REPLACE": broker_pb2.OrderStatus.SUBMITTED,
    "REPLACED": broker_pb2.OrderStatus.SUBMITTED,
    "FILLED": broker_pb2.OrderStatus.FILLED,
    "EXPIRED": broker_pb2.OrderStatus.EXPIRED,
    "NEW": broker_pb2.OrderStatus.SUBMITTED,
    "AWAITING_RELEASE_TIME": broker_pb2.OrderStatus.PENDING,
    "PENDING_ACKNOWLEDGEMENT": broker_pb2.OrderStatus.PENDING,
}

_ASSET_TYPE_MAP = {
    "EQUITY": broker_pb2.AssetClass.STOCK,
    "ETF": broker_pb2.AssetClass.STOCK,
    "INDEX": broker_pb2.AssetClass.STOCK,
    "OPTION": broker_pb2.AssetClass.OPTION,
    "FUTURES": broker_pb2.AssetClass.FUTURE,
    "FUTURE_OPTION": broker_pb2.AssetClass.FUTURE,
    "FOREX": broker_pb2.AssetClass.FX,
    "FIXED_INCOME": broker_pb2.AssetClass.BOND,
    "MUTUAL_FUND": broker_pb2.AssetClass.STOCK,
    "CASH_EQUIVALENT": broker_pb2.AssetClass.STOCK,
}

_ORDER_TYPE_MAP = {
    "MARKET": broker_pb2.OrderType.MARKET,
    "LIMIT": broker_pb2.OrderType.LIMIT,
    "STOP": broker_pb2.OrderType.STOP,
    "STOP_LIMIT": broker_pb2.OrderType.STOP_LIMIT,
    "TRAILING_STOP": broker_pb2.OrderType.TRAILING_STOP,
    "TRAILING_STOP_LIMIT": broker_pb2.OrderType.TRAILING_STOP,
    "MARKET_ON_CLOSE": broker_pb2.OrderType.MARKET,
    "EXERCISE": broker_pb2.OrderType.MARKET,
    "CABINET": broker_pb2.OrderType.LIMIT,
    "NET_DEBIT": broker_pb2.OrderType.LIMIT,
    "NET_CREDIT": broker_pb2.OrderType.LIMIT,
    "NET_ZERO": broker_pb2.OrderType.LIMIT,
}

_TIF_MAP = {
    "DAY": broker_pb2.TimeInForce.DAY,
    "GTC": broker_pb2.TimeInForce.GTC,
    "FOK": broker_pb2.TimeInForce.FOK,
    "IOC": broker_pb2.TimeInForce.IOC,
    "END_OF_WEEK": broker_pb2.TimeInForce.GTC,
    "END_OF_MONTH": broker_pb2.TimeInForce.GTC,
    "NEXT_END_OF_MONTH": broker_pb2.TimeInForce.GTC,
    "UNKNOWN": broker_pb2.TimeInForce.DAY,
}


def map_status(raw: str) -> int:
    if raw in _STATUS_MAP:
        return _STATUS_MAP[raw]
    SCHWAB_NORMALIZE_UNKNOWN_TOTAL.labels(field="status", value=raw).inc()
    return broker_pb2.OrderStatus.SUBMITTED


def map_asset_type(raw: str) -> int:
    if raw in _ASSET_TYPE_MAP:
        return _ASSET_TYPE_MAP[raw]
    SCHWAB_NORMALIZE_UNKNOWN_TOTAL.labels(field="assetType", value=raw).inc()
    return broker_pb2.AssetClass.UNKNOWN_ASSET_CLASS


def map_order_type(raw: str) -> int:
    return _ORDER_TYPE_MAP.get(raw, broker_pb2.OrderType.MARKET)


def map_tif(raw: str) -> int:
    return _TIF_MAP.get(raw, broker_pb2.TimeInForce.DAY)


def normalize_account(raw: dict[str, Any]) -> broker_pb2.Account:
    return broker_pb2.Account(
        account_number=raw["accountNumber"],
        mode=broker_pb2.TradingMode.LIVE,
        currency_base=raw.get("currency", "USD"),
    )


def normalize_summary(raw: dict[str, Any]) -> broker_pb2.AccountSummary:
    balances = raw["securitiesAccount"]["currentBalances"]
    return broker_pb2.AccountSummary(
        net_liquidation=_money(balances["liquidationValue"]),
        total_cash=_money(balances.get("cashBalance", 0)),
        buying_power=_money(balances.get("buyingPower", 0)),
    )


def normalize_position(raw: dict[str, Any]) -> broker_pb2.Position:
    instrument = raw["instrument"]
    symbol = instrument["symbol"]
    asset_class = map_asset_type(instrument.get("assetType", ""))
    qty = str(raw["longQuantity"] - raw.get("shortQuantity", 0))
    avg_cost = _money(raw["averagePrice"])
    market_value = _money(raw["marketValue"])
    unrealized_pnl = _money(raw.get("unrealizedPnL", 0))
    contract = broker_pb2.Contract(symbol=symbol, asset_class=asset_class)
    return broker_pb2.Position(
        contract=contract,
        quantity=qty,
        avg_cost=avg_cost,
        market_value=market_value,
        unrealized_pnl=unrealized_pnl,
    )


def _avg_fill_from_activity(activities: list[dict[str, Any]]) -> tuple[str, str]:
    total_quantity = Decimal("0")
    total_notional = Decimal("0")

    for activity in activities:
        if activity.get("activityType") != "EXECUTION":
            continue
        for execution_leg in activity.get("executionLegs", []):
            quantity = Decimal(str(execution_leg["quantity"]))
            price = Decimal(str(execution_leg["price"]))
            total_quantity += quantity
            total_notional += quantity * price

    if total_quantity == 0:
        return "0", "0"

    return str(total_notional / total_quantity), str(total_quantity)


def normalize_order(raw: dict[str, Any]) -> broker_pb2.Order:
    order_id = str(raw["orderId"])
    legs = raw.get("orderLegCollection", [])
    first_leg = legs[0] if legs else {}
    instrument = first_leg.get("instrument", {})
    symbol = instrument.get("symbol", "")
    asset_class = map_asset_type(instrument.get("assetType", ""))
    contract = broker_pb2.Contract(symbol=symbol, asset_class=asset_class)
    status = map_status(raw.get("status", ""))
    order_type = map_order_type(raw.get("orderType", ""))
    tif = map_tif(raw.get("duration", ""))
    quantity = str(raw.get("quantity", 0))
    limit_price = _money(raw["price"]) if "price" in raw else None
    stop_price = _money(raw["stopPrice"]) if "stopPrice" in raw else None
    activities = raw.get("orderActivityCollection", [])
    if activities:
        avg_price_str, qty_filled_str = _avg_fill_from_activity(activities)
        avg_fill_price_inferred = False
    else:
        avg_price_str = str(raw.get("price", 0))
        qty_filled_str = str(raw.get("filledQuantity", 0))
        avg_fill_price_inferred = True
    avg_fill_price = _money(avg_price_str)

    return broker_pb2.Order(
        order_id=order_id,
        contract=contract,
        status=status,
        order_type=order_type,
        time_in_force=tif,
        quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        quantity_filled=qty_filled_str,
        avg_fill_price=avg_fill_price,
        avg_fill_price_inferred=avg_fill_price_inferred,
    )
