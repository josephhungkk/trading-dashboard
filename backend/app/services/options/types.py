"""Phase 12 options type system: InstrumentMeta and supporting types."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class NonOptionDetails(BaseModel):
    asset_class: str = ""


class OptionDetails(BaseModel):
    asset_class: Literal["OPTION"] = "OPTION"
    underlying_canonical_id: str
    option_type: Literal["CALL", "PUT"]
    strike: Decimal
    expiry: date
    multiplier: int = 100
    style: Literal["AMERICAN", "EUROPEAN"] = "AMERICAN"
    exchange: str = ""
    occ_symbol: str = ""


InstrumentMeta = Annotated[
    OptionDetails | NonOptionDetails,
    Field(discriminator="asset_class"),
]


def parse_instrument_meta(raw: dict[str, object]) -> OptionDetails | NonOptionDetails:
    """Parse a raw meta JSONB dict into the correct InstrumentMeta type."""
    if raw.get("asset_class") == "OPTION":
        return OptionDetails.model_validate(raw)
    return NonOptionDetails.model_validate(raw)


class GreeksSnapshot(BaseModel):
    instrument_id: int
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    iv: Decimal | None = None
    iv_rank: Decimal | None = None


class SubscriptionHandle(BaseModel):
    symbol: str
    broker_id: str
    subscriber_id: str
