from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class EarningsEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instrument_id: int
    canonical_id: str
    announced_at: datetime | None = None
    announced_date: date
    time_of_day: str | None = None
    eps_estimate: Decimal | None = None
    eps_actual: Decimal | None = None
    revenue_estimate: Decimal | None = None
    revenue_actual: Decimal | None = None
    source: str
    source_priority: int = 0
    confirmed: bool = False
    captured_at: datetime
    updated_at: datetime


class EarningsHook(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instrument_id: int
    account_id: uuid.UUID
    jwt_subject: str
    hook_type: str
    minutes_before: int = 30
    bot_id: uuid.UUID | None = None
    enabled: bool = True
    created_at: datetime

    @field_validator("minutes_before")
    @classmethod
    def minutes_before_minimum(cls, v: int) -> int:
        if v < 10:
            raise ValueError("minutes_before must be >= 10")
        return v


class HookAuditRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hook_id: uuid.UUID
    event_id: uuid.UUID
    fired_at: datetime
    outcome: str
    order_id: uuid.UUID | None = None
