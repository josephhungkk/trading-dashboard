from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.ai.capabilities import AICapability


class AdvisorMode(StrEnum):
    OFF = "OFF"
    OBSERVE = "OBSERVE"
    VETO = "VETO"


class AdvisorConfig(BaseModel):
    mode: AdvisorMode = AdvisorMode.OFF
    capability: AICapability = AICapability.REASONING
    local_only: bool = False
    timeout_ms: int = Field(3000, ge=100, le=10_000)
    daily_budget_usd: Decimal = Field(Decimal("5.00"), ge=0)
    max_qps: float = Field(2.0, gt=0)
    auto_pause_threshold: int = Field(0, ge=0)
    auto_pause_window_seconds: int = Field(300, gt=0)
    min_veto_confidence: float = Field(0.0, ge=0.0, le=1.0)

    model_config = {"populate_by_name": True}

    def to_jsonb_dict(self) -> dict:
        """Serialize for JSONB storage: Decimal and enums as strings."""
        d = self.model_dump()
        d["daily_budget_usd"] = str(d["daily_budget_usd"])
        d["capability"] = str(d["capability"])
        d["mode"] = str(d["mode"])
        return d

    @classmethod
    def from_jsonb_dict(cls, data: dict) -> AdvisorConfig:
        """Deserialize from JSONB; daily_budget_usd stored as string."""
        d = dict(data)
        if "daily_budget_usd" in d and isinstance(d["daily_budget_usd"], str):
            d["daily_budget_usd"] = Decimal(d["daily_budget_usd"])
        return cls.model_validate(d)


class OrderIntent(BaseModel):
    """Snapshot of the order as the strategy requested it."""

    canonical_id: str
    side: str
    qty: str
    order_type: str
    limit_price: str | None = None
    stop_price: str | None = None
    tif: str
    algo_strategy: str | None = None
    position_effect: str
    broker_id: str
    account_id: UUID


class ContextSummary(BaseModel):
    """Compact digest stored in bot_advisor_decisions.context_summary JSONB."""

    bar_count: int
    position_count: int
    recent_fill_count: int
    risk_decision_count: int
    params_hash: str
    payload_token_estimate: int


class AdvisorVerdict(BaseModel):
    action: Literal["approve", "veto", "fail_open"]
    reasoning: str = ""
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    advice_tags: list[str] = []


class AdvisorDecision(BaseModel):
    """Mirrors bot_advisor_decisions row."""

    id: int
    bot_id: UUID
    bot_run_id: UUID | None
    account_id: UUID
    canonical_id: str
    intent: dict
    context_summary: ContextSummary
    prompt_version: int
    verdict: str
    reasoning: str
    confidence: float | None
    advice_tags: list[str]
    provider: str | None
    model: str | None
    fallback_chain: list[str]
    latency_ms: int
    ai_completion_ts: datetime | None
    ai_completion_request_id: UUID | None
    account_gate_outcome: str
    account_gate_decision_id: int | None
    effective_mode: str
    created_at: datetime


@dataclasses.dataclass(frozen=True, slots=True)
class AdvisorVetoedResult:
    """Returned from BotContext.place_order when advisor vetoes."""

    decision_id: int
    reasoning: str
    advice_tags: list[str]
