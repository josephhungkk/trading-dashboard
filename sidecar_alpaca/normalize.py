"""Normalize Alpaca client dictionaries into broker protos."""

from __future__ import annotations

from typing import Any

from sidecar_alpaca._generated.broker.v1 import broker_pb2

_ASSET_CLASS_MAP = {
    "STOCK": broker_pb2.AssetClass.STOCK,
    "US_EQUITY": broker_pb2.AssetClass.STOCK,
    "EQUITY": broker_pb2.AssetClass.STOCK,
    "ETF": broker_pb2.AssetClass.ETF,
    "OPTION": broker_pb2.AssetClass.OPTION,
    "CRYPTO": broker_pb2.AssetClass.CRYPTO,
}

_ORDER_SIDE_MAP = {
    "BUY": broker_pb2.OrderSide.BUY,
    "SELL": broker_pb2.OrderSide.SELL,
}

_ORDER_TYPE_MAP = {
    "MARKET": broker_pb2.OrderType.MARKET,
    "LIMIT": broker_pb2.OrderType.LIMIT,
    "STOP": broker_pb2.OrderType.STOP,
    "STOP_LIMIT": broker_pb2.OrderType.STOP_LIMIT,
}

_TIF_MAP = {
    "DAY": broker_pb2.TimeInForce.DAY,
    "GTC": broker_pb2.TimeInForce.GTC,
    "IOC": broker_pb2.TimeInForce.IOC,
    "FOK": broker_pb2.TimeInForce.FOK,
}

_STATUS_MAP = {
    "NEW": broker_pb2.OrderStatus.SUBMITTED,
    "ACCEPTED": broker_pb2.OrderStatus.SUBMITTED,
    "PENDING_NEW": broker_pb2.OrderStatus.PENDING,
    "ACCEPTED_FOR_BIDDING": broker_pb2.OrderStatus.SUBMITTED,
    "PARTIALLY_FILLED": broker_pb2.OrderStatus.PARTIAL,
    "FILLED": broker_pb2.OrderStatus.FILLED,
    "DONE_FOR_DAY": broker_pb2.OrderStatus.SUBMITTED,
    "CANCELED": broker_pb2.OrderStatus.CANCELLED,
    "CANCELLED": broker_pb2.OrderStatus.CANCELLED,
    "EXPIRED": broker_pb2.OrderStatus.CANCELLED,
    "REPLACED": broker_pb2.OrderStatus.SUBMITTED,
    "PENDING_CANCEL": broker_pb2.OrderStatus.PENDING,
    "PENDING_REPLACE": broker_pb2.OrderStatus.PENDING,
    "REJECTED": broker_pb2.OrderStatus.REJECTED,
    "SUSPENDED": broker_pb2.OrderStatus.PENDING,
    "CALCULATED": broker_pb2.OrderStatus.SUBMITTED,
    "STOPPED": broker_pb2.OrderStatus.SUBMITTED,
}


def _money(value: Any, currency: str = "USD") -> broker_pb2.Money:
    return broker_pb2.Money(value=str(value), currency=currency)


def _mode(mode: str) -> int:
    return (
        broker_pb2.TradingMode.PAPER if mode == "paper" else broker_pb2.TradingMode.LIVE
    )


def _asset_class(raw: str) -> int:
    return _ASSET_CLASS_MAP.get(raw.upper(), broker_pb2.AssetClass.ASSET_UNSPECIFIED)


def _order_side(raw: str) -> int:
    return _ORDER_SIDE_MAP.get(raw.upper(), broker_pb2.OrderSide.SIDE_UNSPECIFIED)


def _order_type(raw: str) -> int:
    return _ORDER_TYPE_MAP.get(raw.upper(), broker_pb2.OrderType.TYPE_UNSPECIFIED)


def _tif(raw: str) -> int:
    return _TIF_MAP.get(raw.upper(), broker_pb2.TimeInForce.TIF_UNSPECIFIED)


def _status(raw: str) -> int:
    return _STATUS_MAP.get(raw.upper(), broker_pb2.OrderStatus.STATUS_UNSPECIFIED)


def canonical_to_alpaca_crypto(canonical_id: str) -> str:
    """crypto:BTC:US -> BTC/USD. Raises ValueError on malformed input."""
    parts = canonical_id.split(":")
    if len(parts) < 3 or parts[0] != "crypto":
        raise ValueError(f"not a crypto canonical_id: {canonical_id}")
    return f"{parts[1].upper()}/USD"


def alpaca_crypto_to_canonical(pair: str) -> str:
    """BTC/USD -> crypto:BTC:US."""
    base, _, _quote = pair.partition("/")
    return f"crypto:{base.upper()}:US"


def to_proto_account(
    data: dict[str, Any],
    *,
    gateway_label: str,
    mode: str,
) -> broker_pb2.Account:
    return broker_pb2.Account(
        account_number=str(data["account_number"]),
        mode=_mode(mode),
        gateway_label=gateway_label,
        currency_base=str(data.get("currency") or "USD"),
        account_hash=str(data["account_id"]),
    )


def to_proto_account_summary(data: dict[str, Any]) -> broker_pb2.Summary:
    currency = str(data.get("currency") or "USD")
    return broker_pb2.Summary(
        net_liquidation=_money(data["net_liquidation_value"], currency),
        total_cash=_money(data["cash"], currency),
        buying_power=_money(data["buying_power"], currency),
    )


def to_proto_position(data: dict[str, Any]) -> broker_pb2.Position:
    symbol = str(data["symbol"])
    currency = str(data.get("currency") or "USD")
    contract = broker_pb2.Contract(
        symbol=symbol,
        exchange=str(data.get("exchange") or ""),
        currency=currency,
        asset_class=_asset_class(str(data.get("asset_class") or "")),
        conid=symbol,
        local_symbol=symbol,
        multiplier="1",
    )
    return broker_pb2.Position(
        contract=contract,
        quantity=str(data["qty"]),
        avg_cost=_money(data["avg_cost"], currency),
        market_value=_money(data["market_value"], currency),
        unrealized_pnl=_money(data["unrealized_pnl"], currency),
    )


def to_proto_order(data: dict[str, Any]) -> broker_pb2.Order:
    symbol = str(data["symbol"])
    currency = str(data.get("currency") or "USD")
    limit_price = data.get("limit_price") or "0"
    stop_price = data.get("stop_price") or "0"
    contract = broker_pb2.Contract(
        symbol=symbol,
        currency=currency,
        asset_class=broker_pb2.AssetClass.STOCK,
        conid=symbol,
        local_symbol=symbol,
        multiplier="1",
    )
    return broker_pb2.Order(
        order_id=str(data["id"]),
        contract=contract,
        side=_order_side(str(data.get("side") or "")),
        order_type=_order_type(str(data.get("type") or "")),
        quantity=str(data["qty"]),
        limit_price=_money(limit_price, currency),
        stop_price=_money(stop_price, currency),
        time_in_force=_tif(str(data.get("tif") or "")),
        status=_status(str(data.get("status") or "")),
        quantity_filled=str(data["filled_qty"]),
    )
