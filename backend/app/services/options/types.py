"""InstrumentMeta discriminated union and related data types for Phase 12."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

# "A" = American, "E" = European
NonOptionAssetClass = Literal["", "STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "CRYPTO", "FOREX"]

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


# Extensible: ForexDetails added in Phase 15
InstrumentMeta = Annotated[
    NonOptionDetails | OptionDetails | FutureDetails,
    Field(discriminator="asset_class"),
]

_adapter: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)


def parse_instrument_meta(
    raw: str | dict[str, Any],
) -> NonOptionDetails | OptionDetails | FutureDetails:
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
