"""Normalize Schwab REST JSON payloads into broker protos."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
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
        (
            broker_pb2.AssetClass,
            "UNKNOWN_ASSET_CLASS",
            broker_pb2.AssetClass.ASSET_UNSPECIFIED,
        ),
        (broker_pb2.AssetClass, "FX", broker_pb2.AssetClass.FOREX),
        (broker_pb2.OrderStatus, "CANCELED", broker_pb2.OrderStatus.CANCELLED),
        (broker_pb2.OrderStatus, "PENDING_CANCEL", broker_pb2.OrderStatus.SUBMITTED),
        (broker_pb2.OrderStatus, "EXPIRED", broker_pb2.OrderStatus.CANCELLED),
        (broker_pb2.OrderType, "TRAILING_STOP", broker_pb2.OrderType.ORDER_TYPE_TRAIL),
    )
    for enum_type, alias, value in enum_aliases:
        if not hasattr(enum_type, alias):
            setattr(enum_type, alias, value)


_install_proto_compat_aliases()


def _money(
    value: str | float | int | Decimal, currency: str = "USD"
) -> broker_pb2.Money:
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
    "PENDING_CANCEL": broker_pb2.OrderStatus.SUBMITTED,
    "CANCELED": broker_pb2.OrderStatus.CANCELLED,
    "PENDING_REPLACE": broker_pb2.OrderStatus.SUBMITTED,
    "REPLACED": broker_pb2.OrderStatus.SUBMITTED,
    "FILLED": broker_pb2.OrderStatus.FILLED,
    "EXPIRED": broker_pb2.OrderStatus.CANCELLED,
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
    "FOREX": broker_pb2.AssetClass.FOREX,
    "FIXED_INCOME": broker_pb2.AssetClass.BOND,
    "MUTUAL_FUND": broker_pb2.AssetClass.STOCK,
    "CASH_EQUIVALENT": broker_pb2.AssetClass.STOCK,
}

_ORDER_TYPE_MAP = {
    "MARKET": broker_pb2.OrderType.ORDER_TYPE_MARKET,
    "LIMIT": broker_pb2.OrderType.ORDER_TYPE_LIMIT,
    "STOP": broker_pb2.OrderType.ORDER_TYPE_STOP,
    "STOP_LIMIT": broker_pb2.OrderType.ORDER_TYPE_STOP_LIMIT,
    "TRAILING_STOP": broker_pb2.OrderType.ORDER_TYPE_TRAIL,
    "TRAILING_STOP_LIMIT": broker_pb2.OrderType.ORDER_TYPE_TRAIL,
    "MARKET_ON_CLOSE": broker_pb2.OrderType.ORDER_TYPE_MARKET,
    "EXERCISE": broker_pb2.OrderType.ORDER_TYPE_MARKET,
    "CABINET": broker_pb2.OrderType.ORDER_TYPE_LIMIT,
    "NET_DEBIT": broker_pb2.OrderType.ORDER_TYPE_LIMIT,
    "NET_CREDIT": broker_pb2.OrderType.ORDER_TYPE_LIMIT,
    "NET_ZERO": broker_pb2.OrderType.ORDER_TYPE_LIMIT,
}

_TIF_MAP = {
    "DAY": broker_pb2.TimeInForce.TIF_DAY,
    "GTC": broker_pb2.TimeInForce.TIF_GTC,
    "FOK": broker_pb2.TimeInForce.TIF_FOK,
    "IOC": broker_pb2.TimeInForce.TIF_IOC,
    "END_OF_WEEK": broker_pb2.TimeInForce.TIF_GTC,
    "END_OF_MONTH": broker_pb2.TimeInForce.TIF_GTC,
    "NEXT_END_OF_MONTH": broker_pb2.TimeInForce.TIF_GTC,
    "UNKNOWN": broker_pb2.TimeInForce.TIF_DAY,
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
    return broker_pb2.AssetClass.ASSET_UNSPECIFIED


def map_order_type(raw: str) -> int:
    return _ORDER_TYPE_MAP.get(raw, broker_pb2.OrderType.ORDER_TYPE_MARKET)


def map_tif(raw: str) -> int:
    return _TIF_MAP.get(raw, broker_pb2.TimeInForce.TIF_DAY)


def to_schwab_order_payload(
    *,
    side: str,
    order_type: str,
    tif: str,
    qty: str,
    symbol: str,
    limit_price: str = "",
    stop_price: str = "",
    trail_offset: str = "",
    trail_offset_type: str = "PERCENT",
    trail_limit_offset: str = "",
    exchange: str = "NYSE",
    expiry_date: "str | datetime.date | None" = None,
) -> dict[str, Any]:
    """Translate flat PlaceOrderRequest fields to Schwab JSON payload.

    Phase 8b extends Phase 8a with trailing stops, MOC/MOO/LOC/LOO, and GTD.
    """
    import datetime as _datetime

    _EXCHANGE_MIC: dict[str, str] = {
        "NYSE": "XNYS",
        "NASDAQ": "XNYS",
        "XNYS": "XNYS",
        "XHKG": "XHKG",
        "XLON": "XLON",
    }

    # --- order type → (schwab_order_type, session_override) ---
    _OT_SESSION: dict[str, tuple[str, str]] = {
        "MOC": ("MARKET_ON_CLOSE", "NORMAL"),
        "MOO": ("MARKET_ON_OPEN", "AM"),
        "LOC": ("LIMIT_ON_CLOSE", "NORMAL"),
        "LOO": ("LIMIT_ON_OPEN", "AM"),
    }

    duration_map: dict[str, str] = {
        "DAY": "DAY",
        "GTC": "GOOD_TILL_CANCEL",
        "IOC": "IMMEDIATE_OR_CANCEL",
        "FOK": "FILL_OR_KILL",
        "GTD": "GOOD_TILL_CANCEL",  # Schwab uses GOOD_TILL_CANCEL + cancelTime for GTD
    }

    # Determine Schwab orderType and session from internal order_type
    if order_type in _OT_SESSION:
        schwab_order_type, session = _OT_SESSION[order_type]
    elif order_type in ("TRAIL", "TRAILING_STOP"):
        schwab_order_type = "TRAILING_STOP"
        session = "NORMAL"
    elif order_type in ("TRAIL_LIMIT", "TRAILING_STOP_LIMIT"):
        schwab_order_type = "TRAILING_STOP_LIMIT"
        session = "NORMAL"
    else:
        schwab_order_type = order_type
        session = "NORMAL"

    payload: dict[str, Any] = {
        "orderType": schwab_order_type,
        "session": session,
        "duration": duration_map.get(tif, "DAY"),
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": side,
                "quantity": qty,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }

    # Price fields by order type
    if order_type in ("LOC", "LOO") and limit_price:
        payload["price"] = limit_price
    elif limit_price and order_type not in _OT_SESSION and order_type not in ("TRAIL", "TRAIL_LIMIT", "TRAILING_STOP", "TRAILING_STOP_LIMIT"):
        payload["price"] = limit_price

    if stop_price and order_type not in ("TRAIL", "TRAIL_LIMIT", "TRAILING_STOP", "TRAILING_STOP_LIMIT"):
        payload["stopPrice"] = stop_price

    # Trailing stop fields
    if order_type in ("TRAIL", "TRAILING_STOP", "TRAIL_LIMIT", "TRAILING_STOP_LIMIT"):
        if trail_offset:
            payload["trailingStopOffset"] = trail_offset
        payload["stopPriceLinkType"] = "VALUE" if trail_offset_type == "AMOUNT" else "PERCENT"
        if order_type in ("TRAIL_LIMIT", "TRAILING_STOP_LIMIT") and trail_limit_offset:
            payload["stopPrice"] = trail_limit_offset

    # GTD → GOOD_TILL_CANCEL + cancelTime computed from exchange calendar
    if tif == "GTD":
        payload["duration"] = "GOOD_TILL_CANCEL"
        if expiry_date is None:
            raise ValueError("gtd_order_missing_expiry_date")
        # Resolve expiry_date to a date object
        if isinstance(expiry_date, str):
            exp = _datetime.date.fromisoformat(expiry_date)
        else:
            exp = expiry_date
        # Compute market session close for the given exchange + date
        import exchange_calendars as xcals  # noqa: PLC0415
        mic = _EXCHANGE_MIC.get(exchange, "XNYS")
        cal = xcals.get_calendar(mic)
        session_close = cal.schedule.loc[str(exp)]["market_close"]
        close_dt = session_close.to_pydatetime()
        payload["cancelTime"] = close_dt.isoformat()

    return payload



def to_schwab_oco_payload(order_a: dict, order_b: dict) -> dict:
    """Build a Schwab OCO REST payload from two single-leg order dicts.

    Each dict must contain the same keys accepted by to_schwab_order_payload
    (side, order_type, tif, qty, symbol, and optionally limit_price, etc.).

    The OCO parent carries no top-level orderType/duration/session — those
    live on each child SINGLE order produced by to_schwab_order_payload.

    Validation raises:
        ValueError("oco_legs_symbol_mismatch") — legs target different symbols.
        ValueError("oco_legs_asset_type_mismatch") — legs target different
            asset types (only EQUITY is supported for now).

    Note: Schwab OCO orders are submitted via REST (schwabdev), not via gRPC.
    The orchestrator calls this helper and posts the payload directly.  No
    PlaceOco RPC exists in the proto.
    """
    sym_a = (order_a.get("symbol") or "").upper()
    sym_b = (order_b.get("symbol") or "").upper()
    if sym_a != sym_b:
        raise ValueError("oco_legs_symbol_mismatch")

    # Asset type is currently always EQUITY for Schwab equities; validate
    # to future-proof when options/futures legs are added.
    asset_a = (order_a.get("assetType") or "EQUITY").upper()
    asset_b = (order_b.get("assetType") or "EQUITY").upper()
    if asset_a != asset_b:
        raise ValueError("oco_legs_asset_type_mismatch")

    # Build each child as a complete SINGLE order payload.
    # Pop assetType from the forwarded dict — to_schwab_order_payload does
    # not accept it as a kwarg (it hard-codes EQUITY internally).
    def _build_leg(order: dict) -> dict:
        kwargs = {k: v for k, v in order.items() if k != "assetType"}
        return to_schwab_order_payload(**kwargs)

    return {
        "orderStrategyType": "OCO",
        "childOrderStrategies": [
            _build_leg(order_a),
            _build_leg(order_b),
        ],
    }

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


@dataclass(frozen=True)
class StatusMapping:
    wire_status: str
    rank: int
    terminal: bool
    kind: str = "status"


_NEW_STATUS_MAP: dict[str, StatusMapping] = {
    "AWAITING_PARENT_ORDER": StatusMapping("pending_submit", 0, False),
    "PENDING_ACTIVATION": StatusMapping("pending_submit", 0, False),
    "QUEUED": StatusMapping("submitted", 1, False),
    "WORKING": StatusMapping("submitted", 1, False),
    "PENDING_CANCEL": StatusMapping("cancel_requested", 2, False),
    "PENDING_REPLACE": StatusMapping("modify_requested", 2, False),
    "FILLED": StatusMapping("filled", 4, True),
    "CANCELED": StatusMapping("cancelled", 4, True),
    "REPLACED": StatusMapping("cancelled", 4, True, kind="replaced"),
    "REJECTED": StatusMapping("rejected", 4, True),
    "EXPIRED": StatusMapping("expired", 4, True),
}


def schwab_status_to_wire(schwab_status: str) -> StatusMapping:
    m = _NEW_STATUS_MAP.get(schwab_status)
    if m is None:
        warnings.warn(
            f"unknown schwab status: {schwab_status}", UserWarning, stacklevel=2
        )
        return StatusMapping("submitted", 1, False)
    return m


@dataclass
class FillEvent:
    exec_id: str
    price: Decimal
    quantity: Decimal
    time_iso: str
    avg_fill_price_inferred: bool = False


@dataclass
class NormalizedOrder:
    broker_order_id: str
    client_order_id: str
    status_mapping: StatusMapping
    entered_time_iso: str = ""
    fills: list[FillEvent] = field(default_factory=list)


def schwab_to_wire_order(
    schwab_order: dict[str, Any],
    *,
    client_order_id: str,
) -> NormalizedOrder:
    fills: list[FillEvent] = []
    total_qty = schwab_order.get("quantity")
    market_value = schwab_order.get("marketValue")
    for activity in schwab_order.get("orderActivityCollection") or []:
        if activity.get("executionType") != "FILL":
            continue
        for leg in activity.get("executionLegs") or []:
            inferred = False
            price = leg.get("price")
            if price is None and total_qty and market_value:
                price = Decimal(str(market_value)) / Decimal(str(total_qty))
                inferred = True
            elif price is None:
                continue
            fills.append(
                FillEvent(
                    exec_id=str(leg["legId"]),
                    price=Decimal(str(price)),
                    quantity=Decimal(str(leg["quantity"])),
                    time_iso=leg["time"],
                    avg_fill_price_inferred=inferred,
                )
            )
    return NormalizedOrder(
        broker_order_id=str(schwab_order["orderId"]),
        client_order_id=client_order_id,
        status_mapping=schwab_status_to_wire(schwab_order["status"]),
        entered_time_iso=schwab_order.get("enteredTime", ""),
        fills=fills,
    )
