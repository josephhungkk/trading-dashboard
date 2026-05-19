from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UniverseConfig(BaseModel):
    type: Literal["schwab_screener", "watchlist", "tickers", "instruments"]
    params: dict[str, Any] = Field(default_factory=dict)


class ScanConfig(BaseModel):
    name: str
    universe_config: UniverseConfig
    rule_expr: str
    schedule: str | None = None
    market_hours_gate: bool = False
    exchange: str | None = None
    llm_depth: Literal["quick", "deep"] = "quick"
    alert_id: int | None = None
    enabled: bool = True


class SavedScanRow(BaseModel):
    id: UUID
    name: str
    universe_config: UniverseConfig
    rule_expr: str
    schedule: str | None
    market_hours_gate: bool
    exchange: str | None
    llm_depth: Literal["quick", "deep"]
    alert_id: int | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ScanRunRow(BaseModel):
    id: UUID
    scan_id: UUID | None
    universe_snapshot: list[str]
    rule_expr: str
    candidate_count: int
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None


class CandidateRow(BaseModel):
    id: UUID
    run_id: UUID
    instrument_id: int | None
    canonical_id: str
    matched_at: datetime
    indicator_snapshot: dict[str, Any]
    llm_commentary: str | None
    llm_depth: Literal["quick", "deep"] | None
