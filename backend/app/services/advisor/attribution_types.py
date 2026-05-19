from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True)
class InstrumentAttribution:
    """Minimal instrument data needed by AttributionService."""

    id: int
    multiplier: Decimal
    primary_exchange: str


class AttributionSummary(BaseModel):
    bot_id: UUID
    window: str
    veto_accuracy: float | None
    approve_accuracy: float | None
    avg_avoided_loss_quote: Decimal | None
    avg_missed_gain_quote: Decimal | None
    complete_count: int
    partial_count: int
    pending_count: int
    bars_unavailable_count: int
    unresolvable_count: int
    skipped_count: int
    generated_at: datetime
