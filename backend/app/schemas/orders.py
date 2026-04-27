from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.accounts import BrokerMaintenance

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "STOP"]
OrderTif = Literal["DAY", "GTC"]
OrderStatusEnum = Literal[
    "pending_submit",
    "submitted",
    "partial",
    "filled",
    "cancelled",
    "rejected",
    "expired",
    "inactive",
]
CapStatus = Literal["ok", "near", "exceeded"]
PositionSanityStatus = Literal["ok", "high", "extreme"]


class ContractSummary(BaseModel):
    conid: int
    description: str


class PreviewRequest(BaseModel):
    account_id: UUID
    conid: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    tif: Literal["DAY", "GTC"]
    qty: str = Field(pattern=r"^\d+(\.\d{1,8})?$")
    limit_price: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,8})?$")
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,8})?$")

    @field_validator("qty", "limit_price", "stop_price", mode="before")
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)

    @model_validator(mode="after")
    def _check_order_type_prices(self) -> Self:
        if self.order_type == "MARKET":
            if self.limit_price is not None or self.stop_price is not None:
                raise ValueError("MARKET orders cannot include limit_price or stop_price")
        elif self.order_type == "LIMIT":
            if self.limit_price is None or self.stop_price is not None:
                raise ValueError("LIMIT orders require limit_price and cannot include stop_price")
        elif self.order_type == "STOP":
            if self.stop_price is None or self.limit_price is not None:
                raise ValueError("STOP orders require stop_price and cannot include limit_price")
        return self


class PositionSanityResult(BaseModel):
    current_qty: str
    new_qty_after_fill: str
    sanity_multiplier: str
    status: Literal["ok", "high", "extreme"]
    requires_extra_attestation: bool

    @field_validator("current_qty", "new_qty_after_fill", "sanity_multiplier", mode="before")
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)

    @classmethod
    def classify(
        cls,
        current_qty: Decimal,
        qty: Decimal,
        side: Literal["BUY", "SELL"],
    ) -> PositionSanityResult:
        new_qty_after_fill = current_qty + qty if side == "BUY" else current_qty - qty
        ratio = abs(new_qty_after_fill) / max(abs(current_qty), Decimal("1"))
        status: Literal["ok", "high", "extreme"]
        if ratio <= Decimal("5"):
            status = "ok"
        elif ratio <= Decimal("10"):
            status = "high"
        else:
            status = "extreme"
        return cls(
            current_qty=_format_decimal_8(current_qty),
            new_qty_after_fill=_format_decimal_8(new_qty_after_fill),
            sanity_multiplier=_format_decimal_8(ratio),
            status=status,
            requires_extra_attestation=status == "extreme",
        )


class PreviewResponse(BaseModel):
    nonce: str
    notional: str
    notional_currency: str
    notional_filled_today: str
    daily_notional_cap: str
    max_notional_per_order: str
    cap_status: Literal["ok", "near", "exceeded"]
    daily_cap_status: Literal["ok", "near", "exceeded"]
    position_sanity: PositionSanityResult
    contract_summary: ContractSummary
    warnings: list[str]

    @field_validator(
        "notional",
        "notional_filled_today",
        "daily_notional_cap",
        "max_notional_per_order",
        mode="before",
    )
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)


class PlaceOrderRequest(PreviewRequest):
    client_order_id: UUID
    nonce: str


class OrderResponse(BaseModel):
    id: UUID
    account_id: UUID
    broker_order_id: str | None
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    tif: Literal["DAY", "GTC"]
    qty: str
    limit_price: str | None
    stop_price: str | None
    status: OrderStatusEnum
    filled_qty: str
    avg_fill_price: str | None
    notional: str
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime | None
    submission_state: Literal["submitted", "pending_unknown", "idempotent_retry"] = "submitted"
    events: list[OrderEvent] = []

    @field_validator(
        "qty",
        "limit_price",
        "stop_price",
        "filled_qty",
        "avg_fill_price",
        "notional",
        mode="before",
    )
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
    broker_maintenance: BrokerMaintenance
    kill_switch_active: bool


class OrderEvent(BaseModel):
    broker_order_id: str
    client_order_id: str
    status: OrderStatusEnum
    filled_qty: str
    avg_fill_price: str
    broker_event_at: datetime
    raw_payload: str

    @field_validator("filled_qty", "avg_fill_price", mode="before")
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)


class PolicyResponse(BaseModel):
    account_id: UUID
    max_notional_per_order: str
    daily_notional_cap: str
    notional_filled_today: str
    trade_enabled: bool
    simulator_only: bool
    position_count: int

    @field_validator(
        "max_notional_per_order",
        "daily_notional_cap",
        "notional_filled_today",
        mode="before",
    )
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)


def _coerce_decimal_8(value: object) -> object:
    if isinstance(value, Decimal):
        return _serialize_decimal_8(value)
    return value


def _serialize_decimal_8(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("1e-8")), "f")


def _format_decimal_8(value: Decimal) -> str:
    """Non-Optional sibling of `_serialize_decimal_8` for callers passing
    a guaranteed-non-None Decimal (mypy needs the narrower return type)."""
    return format(value.quantize(Decimal("1e-8")), "f")
