"""Pydantic v2 schemas for Phase 10a risk engine surfaces.

Spec: docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md §3, §6.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ScopeType = Literal["global", "broker", "account"]
LimitKind = Literal[
    "max_daily_loss_currency_base",
    "max_position_concentration_pct",
    "pdt_warn_remaining",
    "min_buying_power_buffer_pct",
]
Verdict = Literal["allow", "warn", "block"]
Side = Literal["buy", "sell"]
AttemptKind = Literal["place_order", "modify_order"]


class _ScopeRule(BaseModel):
    """Mixin enforcing the scope_type ↔ scope_id invariant.

    - scope_type='global'  → scope_id MUST be None
    - scope_type in ('broker','account') → scope_id MUST be a non-empty string
    Mirrors the DB CHECK constraint: ``(scope_type='global') = (scope_id IS NULL)``.
    """

    @model_validator(mode="after")
    def _scope_id_matches_scope_type(self) -> _ScopeRule:
        if self.scope_type == "global" and self.scope_id is not None:
            raise ValueError("scope_id must be NULL when scope_type='global'")
        if self.scope_type in ("broker", "account") and not self.scope_id:
            raise ValueError(f"scope_id is required when scope_type='{self.scope_type}'")
        return self

    # Subclass attribute declarations for type checkers — see RiskLimitCreate /
    # RiskLimitUpdate, which redeclare these as required fields.
    scope_type: ScopeType
    scope_id: str | None


class RiskLimitCreate(_ScopeRule):
    """Request payload to create a new risk limit row."""

    scope_type: ScopeType
    scope_id: str | None = None
    limit_kind: LimitKind
    limit_value: Annotated[Decimal, Field(max_digits=20, decimal_places=8)]
    warn_at_pct: Annotated[
        Decimal | None,
        Field(default=None, max_digits=5, decimal_places=2, ge=0, le=100),
    ] = None
    is_active: bool = True
    notes: Annotated[str, Field(default="", max_length=1000)] = ""


class RiskLimitUpdate(_ScopeRule):
    """Request payload to update an existing risk limit row.

    PUT-semantics: full body required (mirrors Phase 8a MED-2 pattern).
    """

    scope_type: ScopeType
    scope_id: str | None = None
    limit_kind: LimitKind
    limit_value: Annotated[Decimal, Field(max_digits=20, decimal_places=8)]
    warn_at_pct: Annotated[
        Decimal | None,
        Field(default=None, max_digits=5, decimal_places=2, ge=0, le=100),
    ] = None
    is_active: bool = True
    notes: Annotated[str, Field(default="", max_length=1000)] = ""


class RiskLimitOut(BaseModel):
    """Read-side projection of a `risk_limits` row for `/api/risk/limits`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    scope_type: ScopeType
    scope_id: str | None
    limit_kind: LimitKind
    limit_value: Decimal
    warn_at_pct: Decimal | None
    is_active: bool
    notes: str
    created_at: datetime
    updated_at: datetime
    updated_by: str


class AccountKillSwitchToggleRequest(BaseModel):
    """Request payload to toggle an account-level kill switch.

    Reason is required when enabling — operators always document why a
    real-money account was frozen.
    """

    is_enabled: bool
    reason: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def _reason_required_when_enabling(self) -> AccountKillSwitchToggleRequest:
        if self.is_enabled and not self.reason.strip():
            raise ValueError("reason is required when enabling the kill switch")
        return self


class AccountKillSwitchOut(BaseModel):
    """Read-side projection of `account_kill_switches`."""

    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    is_enabled: bool
    reason: str
    enabled_at: datetime | None
    enabled_by: str | None
    updated_at: datetime


class GateBlockerEntry(BaseModel):
    """One reason the risk gate refused a place_order/modify attempt."""

    check: str
    message: str
    code: str


class GateWarningEntry(BaseModel):
    """One advisory emitted by the risk gate (does not block dispatch)."""

    check: str
    message: str
    code: str | None = None
    value: float | None = None
    threshold: float | None = None


class GateVerdict(BaseModel):
    """Aggregated verdict the risk gate returns from `RiskService.evaluate`."""

    final_verdict: Verdict
    blockers: list[GateBlockerEntry] = Field(default_factory=list)
    warnings: list[GateWarningEntry] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)


class RiskDecisionOut(BaseModel):
    """Read-side projection of `risk_decisions` for `/api/risk/decisions`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: uuid.UUID
    instrument_id: int | None
    side: Side
    qty: Decimal
    price: Decimal | None
    order_type: str
    time_in_force: str
    verdict: Verdict
    blockers: list[dict[str, object]]
    warnings: list[dict[str, object]]
    evaluated_at: datetime
    latency_ms: int
    attempt_kind: AttemptKind
    request_id: str
    order_id: uuid.UUID | None
