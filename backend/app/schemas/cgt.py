"""Pydantic v2 request/response models for the CGT API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CgtSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_year: int
    net_gain_gbp: Decimal
    net_loss_gbp: Decimal
    annual_exempt_amount_gbp: Decimal
    used_allowance_gbp: Decimal
    remaining_allowance_gbp: Decimal
    income_total_gbp: Decimal
    disposal_count: int


class S104PoolEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: int
    symbol: str
    qty: Decimal
    total_cost_gbp: Decimal
    pool_avg_cost_gbp: Decimal
    last_updated_at: datetime


class S104PoolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[S104PoolEntry]
    total_count: int


class CgtClassLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: int
    cgt_class_key: str


class PoolSeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    instrument_id: int
    as_of_date: date
    qty: Decimal
    total_cost_gbp: Decimal
    notes: str | None = None


class RecomputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    instrument_id: int


class HmrcFxRateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    period_month: date
    rate_gbp: Decimal
    source: str


class BrokerStatementEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    broker_id: str
    statement_type: str
    period_start: date
    period_end: date
    raw_format: str
    fetched_at: datetime
    imported_at: datetime | None


class ShortObligationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    instrument_id: int
    open_qty: Decimal
    open_proceeds_gbp: Decimal
    status: str
    opened_at: datetime


class DerivativePositionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    instrument_id: int
    side: str
    qty: Decimal
    total_proceeds_gbp: Decimal
    total_cost_gbp: Decimal
    status: str
    opened_at: datetime
