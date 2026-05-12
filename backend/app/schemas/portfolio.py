"""Phase 10b.2 §5.1 — portfolio rollup response schemas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PerAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    broker_id: str
    alias: str
    currency_native: str
    nlv_native: Decimal | None
    nlv_base: Decimal | None
    realized_today_base: Decimal | None
    unrealized_base: Decimal | None
    fx_rate: Decimal | None
    fx_stale: bool = False
    nlv_age_s: float | None
    status: Literal["live", "initialising", "stale", "fx_stale"] = "live"


class AssetClassExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: str
    long_notional_base: Decimal
    short_notional_base: Decimal
    pct_of_nlv: Decimal


class RollupLive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str
    total_nlv_base: Decimal
    total_realized_today_base: Decimal
    total_unrealized_base: Decimal
    history_since: date | None
    accounts: list[PerAccount]
    exposure_by_asset_class: list[AssetClassExposure]
    fx_rates: dict[str, Decimal] = Field(default_factory=dict)
    stale_accounts: list[UUID] = Field(default_factory=list)
    fx_stale_accounts: list[UUID] = Field(default_factory=list)
    partial: bool = False


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    bucket: datetime
    nlv_close_base: Decimal
    nlv_high_base: Decimal | None
    nlv_low_base: Decimal | None


class BucketTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: datetime
    total_nlv_base: Decimal


class RollupCurve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str
    window: Literal["intraday", "30d", "1y"]
    per_account: list[CurvePoint]
    totals: list[BucketTotal]


class InstrumentExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: int
    display_name: str
    exchange: str
    total_qty: Decimal
    notional_base: Decimal
    pct_of_nlv: Decimal
    cap_pct: Decimal | None
    utilisation_pct: Decimal | None
    verdict: Literal["ok", "warn", "block"]


class RollupDrill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: str
    base_currency: str
    instruments: list[InstrumentExposure]
