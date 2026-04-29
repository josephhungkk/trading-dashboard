"""Normalize Futu SDK payloads into broker proto messages."""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, NamedTuple, TypeAlias

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


def position_from_futu_row(row: dict[str, Any]) -> broker_pb2.Position:
    currency = row.get("currency") or "HKD"
    security_type = str(row.get("security_type", ""))
    asset_class = {
        "STOCK": broker_pb2.AssetClass.STOCK,
        "ETF": broker_pb2.AssetClass.ETF,
        "WARRANT": broker_pb2.AssetClass.WARRANT,
        "BWRT": broker_pb2.AssetClass.CBBC,
    }.get(security_type, broker_pb2.AssetClass.ASSET_UNSPECIFIED)

    return broker_pb2.Position(
        contract=broker_pb2.Contract(
            symbol=str(row.get("code", "")),
            exchange=str(row.get("code", "")).split(".", maxsplit=1)[0],
            currency=currency,
            asset_class=asset_class,
            local_symbol=str(row.get("stock_name", "")),
        ),
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
