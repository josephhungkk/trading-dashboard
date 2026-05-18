from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.accounts import BrokerMaintenance
from app.services.algo.schemas import AlgoStrategy

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal[
    "MARKET",
    "LIMIT",
    "STOP",
    "STOP_LIMIT",
    "TRAIL",
    "TRAIL_LIMIT",
    "MOC",
    "MOO",
    "LOC",
    "LOO",
]
OrderTif = Literal["DAY", "GTC", "IOC", "FOK", "GTD"]
TrailOffsetType = Literal["AMOUNT", "PERCENT"]
OrderStatusEnum = Literal[
    "pending_submit",
    "submitted",
    "partial",
    "filled",
    "cancelled",
    "rejected",
    "expired",
    "inactive",
    "modified",
]
CapStatus = Literal["ok", "near", "exceeded"]
PositionSanityStatus = Literal["ok", "high", "extreme"]
SESSION_BOUND_ORDER_TYPES = {"MOC", "MOO", "LOC", "LOO"}
DECIMAL_8_PATTERN = r"^\d+(\.\d{1,8})?$"
DECIMAL_10_PATTERN = r"^\d{1,10}(\.\d{1,10})?$"
# integer max 10 digits = 9_999_999_999 units (sufficient for crypto qty);
# fractional max 10dp matches NUMERIC(20, 10) storage from alembic 0019.


class ContractSummary(BaseModel):
    # IBKR conids are integers; Futu HK contracts are dotted strings (e.g. HK.00700).
    # Accept both — the wire field is rendered verbatim in the trade ticket UI.
    conid: int | str
    description: str


class PreviewRequest(BaseModel):
    account_id: UUID
    conid: str
    side: OrderSide
    order_type: OrderType
    tif: OrderTif
    qty: str | None = Field(default=None, pattern=DECIMAL_10_PATTERN)
    cash_amount: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    limit_price: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    stop_price: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    trail_offset: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    trail_offset_type: TrailOffsetType | None = None
    trail_limit_offset: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    expiry_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    algo_strategy: AlgoStrategy | None = None
    algo_params: dict[str, str] | None = None

    @field_validator(
        "qty",
        mode="before",
    )
    @classmethod
    def _qty_decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_10(value)

    @field_validator(
        "cash_amount",
        "limit_price",
        "stop_price",
        "trail_offset",
        "trail_limit_offset",
        mode="before",
    )
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)

    @model_validator(mode="after")
    def _check_order_type_prices(self) -> Self:
        if (self.qty is None) == (self.cash_amount is None):
            raise ValueError("cash_amount_xor_qty")
        if self.cash_amount is not None:
            if self.side != "BUY":
                raise ValueError("cash_amount_buy_only")
            if self.order_type != "MARKET":
                raise ValueError("cash_amount_market_only")
            if self.tif != "DAY":
                raise ValueError("cash_amount_day_only")
        _validate_order_shape(
            order_type=self.order_type,
            tif=self.tif,
            limit_price=self.limit_price,
            stop_price=self.stop_price,
            trail_offset=self.trail_offset,
            trail_offset_type=self.trail_offset_type,
            trail_limit_offset=self.trail_limit_offset,
            expiry_date=self.expiry_date,
        )
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
    # Phase 10a D3: structured risk-gate verdict surfaces. risk_warnings and
    # risk_blockers carry the GateVerdict.warnings/blockers lists from
    # RiskService.evaluate (mode='preview'). FE renders blockers as a red
    # banner that prevents submit; warnings as a yellow banner that still
    # allows submit. Empty lists when no risk caps configured.
    risk_warnings: list[dict[str, object]] = []
    risk_blockers: list[dict[str, object]] = []

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
    conid: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    tif: OrderTif
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


def _coerce_decimal_10(value: object) -> object:
    if isinstance(value, Decimal):
        return _serialize_decimal_10(value)
    return value


def _serialize_decimal_8(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("1e-8")), "f")


def _serialize_decimal_10(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.0000000001")), "f")


def _format_decimal_8(value: Decimal) -> str:
    """Non-Optional sibling of `_serialize_decimal_8` for callers passing
    a guaranteed-non-None Decimal (mypy needs the narrower return type)."""
    return format(value.quantize(Decimal("1e-8")), "f")


def _format_decimal_10(value: Decimal) -> str:
    """Non-Optional sibling of `_serialize_decimal_10` for qty values."""
    return format(value.quantize(Decimal("0.0000000001")), "f")


def format_qty(value: Decimal, asset_class: str) -> str:
    if asset_class == "crypto":
        return _format_decimal_10(value)
    return _format_decimal_8(value)


def _validate_order_shape(
    *,
    order_type: OrderType,
    tif: OrderTif,
    limit_price: str | None,
    stop_price: str | None,
    trail_offset: str | None,
    trail_offset_type: TrailOffsetType | None,
    trail_limit_offset: str | None,
    expiry_date: str | None,
) -> None:
    trail_fields_present = (
        trail_offset is not None or trail_offset_type is not None or trail_limit_offset is not None
    )

    if expiry_date is not None:
        try:
            date.fromisoformat(expiry_date)
        except ValueError as exc:
            raise ValueError("expiry_date must be a valid ISO date string") from exc

    if order_type in SESSION_BOUND_ORDER_TYPES and tif != "DAY":
        raise ValueError("session_window_closed: session-bound order types require DAY tif")

    if tif == "GTD" and expiry_date is None:
        raise ValueError("GTD orders require expiry_date")
    if tif != "GTD" and expiry_date is not None:
        raise ValueError("expiry_date can only be supplied when tif is GTD")

    if order_type == "MARKET":
        if limit_price is not None or stop_price is not None or trail_fields_present:
            raise ValueError("MARKET orders cannot include price or trail fields")
    elif order_type == "LIMIT":
        if limit_price is None or stop_price is not None or trail_fields_present:
            raise ValueError(
                "LIMIT orders require limit_price and cannot include stop or trail fields"
            )
    elif order_type == "STOP":
        if stop_price is None or limit_price is not None or trail_fields_present:
            raise ValueError(
                "STOP orders require stop_price and cannot include limit or trail fields"
            )
    elif order_type == "STOP_LIMIT":
        if stop_price is None or limit_price is None:
            raise ValueError("STOP_LIMIT orders require stop_price and limit_price")
        if trail_fields_present:
            raise ValueError("STOP_LIMIT orders cannot include trail fields")
        # expiry_date is allowed when tif='GTD' — handled by the tif-side validator above
    elif order_type == "TRAIL":
        if trail_offset is None or trail_offset_type is None:
            raise ValueError("TRAIL orders require trail_offset and trail_offset_type")
        if limit_price is not None or stop_price is not None or trail_limit_offset is not None:
            raise ValueError(
                "TRAIL orders cannot include limit_price, stop_price, or trail_limit_offset"
            )
    elif order_type == "TRAIL_LIMIT":
        if trail_offset is None or trail_offset_type is None or trail_limit_offset is None:
            raise ValueError(
                "TRAIL_LIMIT orders require trail_offset, trail_offset_type, and trail_limit_offset"
            )
        if limit_price is not None or stop_price is not None:
            raise ValueError("TRAIL_LIMIT orders cannot include limit_price or stop_price")
    elif order_type in {"MOC", "MOO"}:
        if limit_price is not None or stop_price is not None or trail_fields_present:
            raise ValueError("MOC and MOO orders cannot include price or trail fields")
    elif order_type in {"LOC", "LOO"}:
        if limit_price is None:
            raise ValueError("LOC and LOO orders require limit_price")
        if stop_price is not None or trail_fields_present:
            raise ValueError("LOC and LOO orders cannot include stop or trail fields")


class OrderModifyRequest(BaseModel):
    """PUT /api/orders/{id} body. account_id/conid/side immutable."""

    model_config = ConfigDict(extra="forbid")

    nonce: str = Field(min_length=1, max_length=128)
    qty: str = Field(pattern=DECIMAL_10_PATTERN)
    limit_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    order_type: OrderType
    tif: OrderTif
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    trail_offset: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    trail_offset_type: TrailOffsetType | None = None
    trail_limit_offset: str | None = Field(default=None, pattern=DECIMAL_8_PATTERN)
    expiry_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    algo_strategy: AlgoStrategy | None = None
    algo_params: dict[str, str] | None = None  # accepted but ignored server-side (§5.3a)

    @field_validator(
        "qty",
        mode="before",
    )
    @classmethod
    def _qty_decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_10(value)

    @field_validator(
        "limit_price",
        "stop_price",
        "trail_offset",
        "trail_limit_offset",
        mode="before",
    )
    @classmethod
    def _decimal_inputs_to_wire_string(cls, value: object) -> object:
        return _coerce_decimal_8(value)

    @model_validator(mode="after")
    def _check_order_type_prices(self) -> Self:
        _validate_order_shape(
            order_type=self.order_type,
            tif=self.tif,
            limit_price=self.limit_price,
            stop_price=self.stop_price,
            trail_offset=self.trail_offset,
            trail_offset_type=self.trail_offset_type,
            trail_limit_offset=self.trail_limit_offset,
            expiry_date=self.expiry_date,
        )
        return self


class OrderBracketLeg(BaseModel):
    id: UUID
    leg: Literal["stop_loss", "take_profit"]
    broker_order_id: str
    status: str


class OrderBracketParent(BaseModel):
    id: UUID
    client_order_id: UUID
    broker_order_id: str
    status: str


class OrderBracketResponse(BaseModel):
    parent: OrderBracketParent
    children: list[OrderBracketLeg]
    oca_group: str


class OrderBracketRequest(BaseModel):
    # bracket children always carry qty; notional flows only to parent leg
    # (enforced at OCO endpoint, not schema)
    nonce: str = Field(min_length=1, max_length=128)
    account_id: UUID
    client_order_id: UUID
    conid: str = Field(pattern=r"^\d+$")
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT"]
    tif: Literal["DAY", "GTC"]
    qty: str = Field(pattern=DECIMAL_10_PATTERN)
    limit_price: str = Field(pattern=r"^\d+(\.\d+)?$")
    stop_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    target_price: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")


class FillResponse(BaseModel):
    id: UUID
    order_id: UUID
    exec_id: str
    qty: str
    price: str
    currency: str = Field(min_length=3, max_length=3)
    executed_at: datetime
    commission: str | None = None
    commission_currency: str | None = Field(default=None, min_length=3, max_length=3)


class FillListResponse(BaseModel):
    fills: list[FillResponse]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# T-O.6: OCO (one-cancels-other) order schemas
# ---------------------------------------------------------------------------


class OcoOrderRequest(BaseModel):
    """Two-leg one-cancels-other order request."""

    order_a: PreviewRequest  # reuse existing PreviewRequest shape
    order_b: PreviewRequest
    nonce: str = Field(min_length=1, max_length=128)


class OcoOrderResponse(BaseModel):
    """Response after successfully placing both OCO legs."""

    oco_link_id: str  # UUID of the oco_links row (server-generated — Pattern E)
    order_id_a: str  # broker_order_id of leg A
    order_id_b: str  # broker_order_id of leg B
