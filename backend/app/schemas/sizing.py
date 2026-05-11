"""Phase 10b.1 position-sizing schemas.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §3.3.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.risk import GateVerdict, Side


class SizingMethod(StrEnum):
    fixed_fractional = "fixed_fractional"
    risk_per_trade = "risk_per_trade"
    vol_targeted = "vol_targeted"


_STRICT = ConfigDict(extra="forbid")


class FixedFractionalInputs(BaseModel):
    model_config = _STRICT

    kind: Literal["fixed_fractional"] = "fixed_fractional"
    risk_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4),
    ]
    price: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]


class RiskPerTradeInputs(BaseModel):
    model_config = _STRICT

    kind: Literal["risk_per_trade"] = "risk_per_trade"
    risk_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4),
    ]
    entry: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]
    stop: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]


class VolTargetedInputs(BaseModel):
    model_config = _STRICT

    kind: Literal["vol_targeted"] = "vol_targeted"
    target_vol_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("200"), max_digits=10, decimal_places=4),
    ]
    price: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=20, decimal_places=8)]
    vol_override_pct: Annotated[
        Decimal | None,
        Field(default=None, gt=Decimal("0"), lt=Decimal("500"), max_digits=10, decimal_places=4),
    ] = None


SizingInputs = Annotated[
    FixedFractionalInputs | RiskPerTradeInputs | VolTargetedInputs,
    Field(discriminator="kind"),
]


class SizingRequest(BaseModel):
    """Sizing request. Caller supplies EITHER ``instrument_id`` (when known —
    e.g., the /trade/sizing page after selecting from the instrument
    selector) OR ``conid`` + ``broker_id`` (when working in broker-symbol
    space — e.g., TradeTicketModal). The service resolves
    conid→instrument_id internally via InstrumentResolver."""

    model_config = _STRICT

    account_id: UUID
    instrument_id: int | None = None
    conid: str | None = None
    broker_id: str | None = None
    method: SizingMethod
    side: Side
    inputs: SizingInputs

    @model_validator(mode="after")
    def _check_instrument_identity(self) -> SizingRequest:
        if self.instrument_id is None and (self.conid is None or self.broker_id is None):
            raise ValueError("either instrument_id OR (conid + broker_id) must be provided")
        return self


class MethodBreakdown(BaseModel):
    nlv_base: Decimal
    fx_rate: Decimal
    price_base: Decimal
    account_currency: str
    atr14: Decimal | None = None
    realized_vol14_annualized: Decimal | None = None
    risk_per_share_base: Decimal | None = None
    vol_source: Literal["realized", "override", "n/a"] = "n/a"


class SizingResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    suggested_qty: Decimal
    base_currency_notional: Decimal
    method: SizingMethod
    breakdown: MethodBreakdown
    risk_verdict: GateVerdict


class SizingDefaults(BaseModel):
    """Per-account stored defaults retrieved from app_config namespace risk_sizing."""

    method: SizingMethod = SizingMethod.fixed_fractional
    fixed_fractional_risk_pct: Decimal = Decimal("2.00")
    risk_per_trade_risk_pct: Decimal = Decimal("1.00")
    vol_targeted_target_vol_pct: Decimal = Decimal("15.00")


class SizingDefaultsUpdate(BaseModel):
    """PUT payload — full body (PUT semantics), CSRF nonce on the endpoint."""

    model_config = _STRICT

    method: SizingMethod
    fixed_fractional_risk_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4),
    ]
    risk_per_trade_risk_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("100"), max_digits=10, decimal_places=4),
    ]
    vol_targeted_target_vol_pct: Annotated[
        Decimal,
        Field(gt=Decimal("0"), lt=Decimal("200"), max_digits=10, decimal_places=4),
    ]
