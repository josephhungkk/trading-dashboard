"""Request/response schemas for the Phase 12 options API."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class OptionChainRowSchema(BaseModel):
    conid: str
    strike: str
    put_call: Literal["C", "P"]
    bid: str
    ask: str
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    open_interest: int
    volume: int
    multiplier: int
    exchange: str
    style: Literal["A", "E"]


class OptionChainResponse(BaseModel):
    calls: list[OptionChainRowSchema]
    puts: list[OptionChainRowSchema]
    source: str
    fetched_at_ms: int
    stale: bool = False


class OptionExpirationsResponse(BaseModel):
    expiry_dates: list[date]


class ExerciseElectionRequest(BaseModel):
    account_id: uuid.UUID
    instrument_id: int
    action: Literal["EXERCISE", "DO_NOT_EXERCISE", "LAPSE"]
    qty: Decimal = Field(gt=0)
    idempotency_key: uuid.UUID


class ExerciseElectionResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: uuid.UUID
    status: str
    broker_ref: str | None = None


class OptionChainSourcesRequest(BaseModel):
    sources: dict[str, list[str]]


class OptionSubBudgetsRequest(BaseModel):
    budgets: dict[str, int]


class TradingLevelRequest(BaseModel):
    level: int = Field(ge=1, le=4)
