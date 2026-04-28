"""Pydantic broker boundary models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.ibkr_maintenance import BrokerMaintenance

TradingMode = Literal["MODE_UNSPECIFIED", "LIVE", "PAPER"]
AssetClass = Literal[
    "ASSET_UNSPECIFIED",
    "STOCK",
    "ETF",
    "OPTION",
    "FUTURE",
    "FOREX",
    "CRYPTO",
    "BOND",
    "MUTUAL_FUND",
    "WARRANT",
]
OrderSide = Literal["SIDE_UNSPECIFIED", "BUY", "SELL"]
OrderType = Literal["TYPE_UNSPECIFIED", "MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
TimeInForce = Literal["TIF_UNSPECIFIED", "DAY", "GTC", "IOC", "FOK"]
OrderStatus = Literal[
    "STATUS_UNSPECIFIED",
    "PENDING",
    "SUBMITTED",
    "PARTIAL",
    "FILLED",
    "CANCELLED",
    "REJECTED",
]


class HealthResponse(BaseModel):
    label: str
    gateway_connected: bool
    gateway_version: str
    last_tick_at: datetime | None
    sidecar_version: str


class Account(BaseModel):
    account_number: str
    mode: TradingMode
    gateway_label: str
    # Empty string means "BASE tag not yet cached on the sidecar" - the
    # discoverer still upserts the row; the AccountResponse layer surfaces
    # it as "" and the frontend renders a placeholder. Once the sidecar's
    # ib_async cache catches the BASE tag, subsequent ticks overwrite.
    currency_base: str = Field(default="", max_length=3)


class Money(BaseModel):
    value: str
    currency: str


class Summary(BaseModel):
    net_liquidation: Money
    total_cash: Money
    realized_pnl: Money
    unrealized_pnl: Money
    buying_power: Money
    updated_at: datetime | None


class Contract(BaseModel):
    symbol: str
    exchange: str
    currency: str
    asset_class: AssetClass
    conid: str
    local_symbol: str
    multiplier: str = ""


class Position(BaseModel):
    contract: Contract
    quantity: str
    avg_cost: Money
    market_price: Money
    market_value: Money
    unrealized_pnl: Money
    realized_pnl_today: Money
    daily_pnl: Money


class Order(BaseModel):
    order_id: str
    contract: Contract
    side: OrderSide
    order_type: OrderType
    quantity: str
    limit_price: Money
    stop_price: Money
    time_in_force: TimeInForce
    status: OrderStatus
    quantity_filled: str
    avg_fill_price: Money
    submitted_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class PlaceOrderResult:
    broker_order_id: str
    status: str


@dataclass(frozen=True)
class ModifyOrderResult:
    broker_order_id: str
    status: str


@dataclass(frozen=True)
class BracketResult:
    parent_broker_order_id: str
    stop_loss_broker_order_id: str  # "" if not requested
    take_profit_broker_order_id: str  # "" if not requested
    status: str


@dataclass(frozen=True)
class OrderEventMessage:
    broker_order_id: str
    client_order_id: str
    status: str
    filled_qty: str
    avg_fill_price: str
    broker_event_at: datetime | None
    raw_payload: str
    exec_id: str = ""
    kind: str = ""


class AccountResponse(BaseModel):
    id: UUID
    broker_id: Literal["ibkr", "futu", "schwab"]
    alias: str | None
    mode: Literal["live", "paper"]
    # "" is allowed (BASE tag not yet cached on the sidecar). Frontend
    # renders a placeholder until the discoverer overwrites with a real
    # 3-letter code on a subsequent tick.
    currency_base: str = Field(default="", max_length=3)
    display_order: int
    # Phase 5a additions (spec section 3.1):
    nlv: str | None = Field(default=None)
    nlv_currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    nlv_at: datetime | None = Field(default=None)


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    degraded_sidecars: list[str]
    broker_maintenance: BrokerMaintenance  # New in 5a (spec §3.2)


class AccountAliasUpdate(BaseModel):
    alias: str = Field(min_length=1, max_length=64, pattern=r"^[\w\s\-.&]+$")
