"""Normalize Futu SDK payloads into broker proto messages."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, NamedTuple, TypeAlias
from zoneinfo import ZoneInfo

from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2


class AccountSkipReason(StrEnum):
    UNKNOWN_TRD_ENV = "unknown_trd_env"


class AccountMapped(NamedTuple):
    account: broker_pb2.Account


class AccountSkipped(NamedTuple):
    reason: AccountSkipReason


AccountResult: TypeAlias = AccountMapped | AccountSkipped  # noqa: UP040


def account_from_futu_row(row: dict[str, Any]) -> AccountResult:
    """Map one futu acc_list row to proto Account, or skip on unknown trd_env."""
    trd_env = row.get("trd_env")
    if trd_env == "REAL":
        mode = broker_pb2.TradingMode.LIVE
    elif trd_env == "SIMULATE":
        mode = broker_pb2.TradingMode.PAPER
    else:
        return AccountSkipped(AccountSkipReason.UNKNOWN_TRD_ENV)

    return AccountMapped(
        broker_pb2.Account(
            account_number=str(row["acc_id"]),
            mode=mode,
            gateway_label="futu",
        )
    )


def _money(value: str | int | float | None, currency: str) -> broker_pb2.Money:
    if value is None or value == "":
        d = Decimal("0")
    else:
        d = Decimal(str(value))
    d = d.quantize(Decimal("1e-8"))
    return broker_pb2.Money(value=format(d, "f"), currency=(currency or "HKD"))


def hk_local_to_utc_timestamp(value: str | None) -> Timestamp:
    ts = Timestamp()
    if value is None or value == "":
        return ts

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
    ts.FromDatetime(dt.astimezone(UTC))
    return ts


def contract_from_futu_row(row: dict[str, Any]) -> broker_pb2.Contract:
    security_type = str(row.get("security_type", ""))
    asset_class = {
        "STOCK": broker_pb2.AssetClass.STOCK,
        "ETF": broker_pb2.AssetClass.ETF,
        "WARRANT": broker_pb2.AssetClass.WARRANT,
        "BWRT": broker_pb2.AssetClass.CBBC,
    }.get(security_type, broker_pb2.AssetClass.ASSET_UNSPECIFIED)

    code = str(row.get("code", ""))
    return broker_pb2.Contract(
        symbol=code,
        exchange=code.split(".", maxsplit=1)[0],
        currency=row.get("currency") or "HKD",
        asset_class=asset_class,
        local_symbol=str(row.get("stock_name", "")),
    )


def position_from_futu_row(row: dict[str, Any]) -> broker_pb2.Position:
    currency = row.get("currency") or "HKD"

    return broker_pb2.Position(
        contract=contract_from_futu_row(row),
        quantity=str(row.get("qty", "0")),
        avg_cost=_money(row.get("cost_price", "0"), currency),
        market_price=_money(row.get("nominal_price", "0"), currency),
        market_value=_money(row.get("market_val", "0"), currency),
        unrealized_pnl=_money(row.get("unrealized_pl", "0"), currency),
        realized_pnl_today=_money(row.get("realized_pl", "0"), currency),
        daily_pnl=_money(row.get("today_pl", "0"), currency),
    )


def summary_from_futu_row(
    row: dict[str, Any],
    *,
    account_number: str,
) -> broker_pb2.Summary:
    del account_number  # reserved for L2 currency override
    currency = row.get("currency") or "HKD"
    return broker_pb2.Summary(
        net_liquidation=_money(row.get("total_assets", "0"), currency),
        total_cash=_money(row.get("cash", "0"), currency),
        realized_pnl=_money(row.get("realized_pl", "0"), currency),
        unrealized_pnl=_money(row.get("unrealized_pl", "0"), currency),
        buying_power=_money(row.get("power", "0"), currency),
    )


def side_from_futu_side(value: str | None) -> broker_pb2.OrderSide:
    return {
        "BUY": broker_pb2.OrderSide.BUY,
        "SELL": broker_pb2.OrderSide.SELL,
    }.get(str(value or ""), broker_pb2.OrderSide.SIDE_UNSPECIFIED)


def type_from_futu_order_type(value: str | None) -> broker_pb2.OrderType:
    return {
        "MARKET": broker_pb2.OrderType.MARKET,
        "NORMAL": broker_pb2.OrderType.LIMIT,
        "LIMIT": broker_pb2.OrderType.LIMIT,
        "AUCTION_LIMIT": broker_pb2.OrderType.LIMIT,
        "SPECIAL_LIMIT": broker_pb2.OrderType.LIMIT,
        "SPECIAL_LIMIT_ALL": broker_pb2.OrderType.LIMIT,
        "ABSOLUTE_LIMIT": broker_pb2.OrderType.LIMIT,
        "STOP": broker_pb2.OrderType.STOP,
        "STOP_LIMIT": broker_pb2.OrderType.STOP_LIMIT,
    }.get(str(value or ""), broker_pb2.OrderType.TYPE_UNSPECIFIED)


def tif_from_futu_time_in_force(value: str | None) -> broker_pb2.TimeInForce:
    return {
        "DAY": broker_pb2.TimeInForce.DAY,
        "GTC": broker_pb2.TimeInForce.GTC,
        "GOOD_TILL_CANCEL": broker_pb2.TimeInForce.GTC,
        "IOC": broker_pb2.TimeInForce.IOC,
        "FOK": broker_pb2.TimeInForce.FOK,
    }.get(str(value or ""), broker_pb2.TimeInForce.TIF_UNSPECIFIED)


def status_from_futu_status(value: str | None) -> broker_pb2.OrderStatus:
    return {
        "UNSUBMITTED": broker_pb2.OrderStatus.PENDING,
        "SUBMITTING": broker_pb2.OrderStatus.PENDING,
        "WAITING_SUBMIT": broker_pb2.OrderStatus.SUBMITTED,
        "SUBMITTED": broker_pb2.OrderStatus.SUBMITTED,
        "FILLED_PART": broker_pb2.OrderStatus.PARTIAL,
        "FILLED_ALL": broker_pb2.OrderStatus.FILLED,
        "CANCELLED_PART": broker_pb2.OrderStatus.CANCELLED,
        "CANCELLED_ALL": broker_pb2.OrderStatus.CANCELLED,
        "FAILED": broker_pb2.OrderStatus.REJECTED,
        "DISABLED": broker_pb2.OrderStatus.REJECTED,
        "CANCELLING_ALL": broker_pb2.OrderStatus.PENDING,
        "CANCELLING_PART": broker_pb2.OrderStatus.PARTIAL,
        "SUBMIT_FAILED": broker_pb2.OrderStatus.REJECTED,
        "FILL_CANCELLED": broker_pb2.OrderStatus.CANCELLED,
        "TIMEOUT": broker_pb2.OrderStatus.REJECTED,
    }.get(str(value or ""), broker_pb2.OrderStatus.STATUS_UNSPECIFIED)


def status_string_from_futu(value: str | None) -> str:
    status = status_from_futu_status(value)
    if status == broker_pb2.OrderStatus.STATUS_UNSPECIFIED:
        return ""
    return str(broker_pb2.OrderStatus.Name(status)).lower()


def order_event_from_futu_order_row(
    row: dict[str, Any],
) -> broker_pb2.OrderEventMessage:
    ts = (
        hk_local_to_utc_timestamp(row["updated_time"])
        if row.get("updated_time")
        else None
    )
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row["order_id"]),
        client_order_id=row.get("remark", "") or "",
        status=status_string_from_futu(row.get("order_status", "")),
        filled_qty=str(row.get("dealt_qty", "0")),
        avg_fill_price=str(row.get("dealt_avg_price", "0")),
        event_at=ts,
        kind="status",
    )


def order_event_from_futu_deal_row(
    row: dict[str, Any],
) -> broker_pb2.OrderEventMessage:
    ts = (
        hk_local_to_utc_timestamp(row["create_time"])
        if row.get("create_time")
        else None
    )
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row.get("order_id", "")),
        filled_qty=str(row.get("qty", "0")),
        avg_fill_price=str(row.get("price", "0")),
        event_at=ts,
        exec_id=str(row.get("deal_id", "")),
        kind="exec_details",
    )


def commission_event_from_futu_deal_row(
    row: dict[str, Any],
) -> broker_pb2.OrderEventMessage:
    ts = (
        hk_local_to_utc_timestamp(row["create_time"])
        if row.get("create_time")
        else None
    )
    return broker_pb2.OrderEventMessage(
        broker_order_id=str(row.get("order_id", "")),
        event_at=ts,
        exec_id=str(row.get("deal_id", "")),
        kind="commission_report",
        raw_payload=str(
            {
                "commission": str(row.get("commission", "0")),
                "currency": row.get("currency", "HKD"),
            }
        ),
    )


def order_from_futu_row(row: dict[str, Any]) -> broker_pb2.Order:
    currency = row.get("currency") or "HKD"
    return broker_pb2.Order(
        order_id=str(row.get("order_id", "")),
        contract=contract_from_futu_row(row),
        side=side_from_futu_side(row.get("trd_side")),
        order_type=type_from_futu_order_type(row.get("order_type")),
        quantity=str(row.get("qty", "0")),
        limit_price=_money(row.get("price", "0"), currency),
        stop_price=_money(row.get("aux_price", "0"), currency),
        time_in_force=tif_from_futu_time_in_force(row.get("time_in_force")),
        status=status_from_futu_status(row.get("order_status")),
        quantity_filled=str(row.get("dealt_qty", "0")),
        avg_fill_price=_money(row.get("dealt_avg_price", "0"), currency),
        submitted_at=hk_local_to_utc_timestamp(row.get("create_time")),
        updated_at=hk_local_to_utc_timestamp(row.get("updated_time")),
    )
