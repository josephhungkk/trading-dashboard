"""InstrumentMeta discriminated union and related data types for Phase 12."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from datetime import time as time_type
from decimal import Decimal
from enum import IntEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

# "A" = American, "E" = European
NonOptionAssetClass = Literal["", "STOCK", "ETF", "INDEX", "WARRANT", "CBBC"]

_CLAMP_MAX = Decimal("9999.999999")
_CLAMP_MIN = Decimal("-9999.999999")


class NonOptionDetails(BaseModel):
    """All non-option instruments. Existing {} rows deserialise with asset_class=''."""

    asset_class: NonOptionAssetClass = ""


class OptionDetails(BaseModel):
    """Options contract details stored in instruments.meta JSONB."""

    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str
    strike: Decimal
    expiry: date
    put_call: Literal["C", "P"]
    multiplier: Decimal  # required — no default; sidecar must populate
    style: Literal["A", "E"]  # "A" = American, "E" = European; required — no default


class FutureDetails(BaseModel):
    """Futures contract details stored in instruments.meta JSONB."""

    asset_class: Literal["FUTURE"] = "FUTURE"
    contract_month: str
    tick_size: Decimal
    tick_value: Decimal
    multiplier: Decimal
    first_notice_day: date | None
    expiry: date
    settlement_type: Literal["CASH", "PHYSICAL"]
    exchange: str
    underlying_symbol: str


class ForexDetails(BaseModel):
    """IDEALPRO spot FX pair details — Phase 15a."""

    asset_class: Literal["FOREX"] = "FOREX"
    base_currency: str
    quote_currency: str
    pip_size: Decimal
    contract_size: Decimal | None = None  # None for spot (notional-based)
    trading_hours: str  # human-readable e.g. "Sun 17:00 - Fri 17:00 ET"


class CryptoDetails(BaseModel):
    """Paxos crypto asset details — Phase 15b."""

    asset_class: Literal["CRYPTO"] = "CRYPTO"
    base_asset: str  # e.g. "BTC"
    quote_asset: str  # e.g. "USD"
    min_qty: Decimal
    qty_step: Decimal
    min_notional: Decimal | None = None  # e.g. 1.00 USD; None if not specified


class CouponFrequency(IntEnum):
    ZERO_COUPON = 0
    ANNUAL = 1
    SEMI_ANNUAL = 2
    QUARTERLY = 4
    MONTHLY = 12


class BondDetails(BaseModel):
    asset_class: Literal["BOND"] = "BOND"
    cusip: str | None = None
    isin: str | None = None
    issuer_id: str | None = None
    coupon_rate: Decimal
    coupon_frequency: CouponFrequency
    maturity_date: date
    face_value: Decimal
    issue_date: date | None = None
    bond_type: str
    currency: str
    settlement_days: int = 2
    callable: bool = False
    yield_to_maturity: Decimal | None = None
    duration: Decimal | None = None
    credit_rating: str | None = None


class MutualFundDetails(BaseModel):
    asset_class: Literal["MUTUAL_FUND"] = "MUTUAL_FUND"
    isin: str | None = None
    cusip: str | None = None
    fund_family: str
    fund_type: str
    currency: str
    min_investment: Decimal
    min_subsequent: Decimal
    settlement_days: int = 1
    allows_fractional: bool = True
    cutoff_time_et: time_type
    expense_ratio: Decimal | None = None
    nav_currency: str


class CFDDetails(BaseModel):
    asset_class: Literal["CFD"] = "CFD"
    underlying_type: str
    underlying_symbol: str
    underlying_conid: str | None = None
    currency: str
    tick_size: Decimal
    qty_step: Decimal = Decimal("1")
    multiplier: Decimal
    margin_rate: Decimal
    overnight_rate_long: Decimal
    overnight_rate_short: Decimal
    max_leverage: Decimal
    listed_country: str | None = None
    exchange: str = "IBCFD"


InstrumentMeta = Annotated[
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails
    | MutualFundDetails
    | CFDDetails,
    Field(discriminator="asset_class"),
]

_adapter: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)


def parse_instrument_meta(
    raw: str | dict[str, Any],
) -> (
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails
    | MutualFundDetails
    | CFDDetails
):
    """Parse instruments.meta JSONB dict into a typed model. Raises ValidationError on bad shape."""
    data: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else raw
    if "asset_class" not in data:
        data = {**data, "asset_class": ""}
    if data.get("asset_class") == "OPTION":
        data = _normalize_option_meta(data)
    return _adapter.validate_python(data)


def _normalize_option_meta(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy sidecar option meta aliases while keeping OptionDetails strict."""
    normalized = dict(raw)
    if "expiry" not in normalized and "expiry_iso" in normalized:
        normalized["expiry"] = normalized["expiry_iso"]
    if normalized.get("put_call") == "CALL":
        normalized["put_call"] = "C"
    elif normalized.get("put_call") == "PUT":
        normalized["put_call"] = "P"
    normalized.setdefault("underlying_canonical_id", "")
    normalized.setdefault("style", "A")
    return normalized


@dataclass(frozen=True)
class GreeksSnapshot:
    """Greeks for a single option contract. Clamps extreme values to avoid DB overflow."""

    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    rho: Decimal
    iv: Decimal
    iv_rank: Decimal | None = None

    def __post_init__(self) -> None:
        for fname in ("delta", "gamma", "theta", "vega", "rho", "iv"):
            val = getattr(self, fname)
            if val < _CLAMP_MIN or val > _CLAMP_MAX:
                object.__setattr__(self, fname, max(_CLAMP_MIN, min(_CLAMP_MAX, val)))
                # option_greeks_clamped_total counter wired in Chunk F (metrics setup)


@dataclass
class SubscriptionHandle:
    """Tracks a single option strike subscription.

    conid: broker-native source symbol (IBKR conid, OCC symbol, Futu code)
    canonical_id: set once the instrument row has been created (order-intent path)
    channel: Redis channel being subscribed to
    """

    conid: str
    canonical_id: str | None
    channel: str
