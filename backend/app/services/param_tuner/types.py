from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TunerTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    BACKTESTING = "backtesting"
    RANKED = "ranked"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class BacktestResultSnapshot(BaseModel):
    sharpe: float | None = None
    mar: float | None = None
    max_dd: float | None = None
    win_rate: float | None = None
    avg_trade_pnl: Decimal = Decimal("0")
    forced_close_pnl: Decimal = Decimal("0")
    total_trades: int = 0


class ParamCandidate(BaseModel):
    params: dict[str, Any]
    backtest_job_id: UUID | None = None
    backtest_result: BacktestResultSnapshot | None = None
    rank: int | None = None
    delta_vs_current: dict[str, str] = Field(default_factory=dict)


class ParamSuggestion(BaseModel):
    id: UUID
    bot_id: UUID
    triggered_by: TunerTrigger
    status: SuggestionStatus
    strategy_params_current: dict[str, Any]
    ai_reasoning: str | None = None
    candidates: list[ParamCandidate] = Field(default_factory=list)
    approved_candidate_index: int | None = None
    approved_by: str | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CandidateListResponse(BaseModel):
    candidates: list[dict[str, Any]]
    reasoning: str


class TunerAlreadyActiveError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class TunerCostCeilingError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class SupervisorRestartError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
