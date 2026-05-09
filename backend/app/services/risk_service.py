"""Phase 10a — Risk gate evaluator.

The 4th validation station in the order write path (after kill-switch /
maintenance / capability; before broker dispatch). Spec:
``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md``.

Deterministic given inputs (no global singletons): caller supplies an
``EvaluationContext`` and four injected dependencies (db, redis, config,
sidecar). Returns a ``GateVerdict``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import RiskLimit
from app.schemas.risk import GateVerdict

log = structlog.get_logger(__name__)

EvalMode = Literal["preview", "place_order", "modify_order"]
Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs to the risk gate. Pure data; no service references."""

    account_id: uuid.UUID
    broker_id: str
    instrument_id: int | None
    side: Side
    qty: Decimal
    price: Decimal | None
    order_type: str
    time_in_force: str
    request_id: str
    currency_base: str  # account base currency for max-loss conversion


class RiskService:
    """Risk-gate evaluator.

    Lookup walk for ``risk_limits``: ``(account, kind)`` → ``(broker, kind)`` →
    ``(global, kind)``. First active hit wins. ``evaluate(ctx, mode)`` returns
    a ``GateVerdict`` aggregated from the seven checks (added in B2-B7).
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: object,
        config: object,
        sidecar: object,
    ) -> None:
        self._db = db
        self._redis = redis
        self._config = config
        self._sidecar = sidecar

    async def _resolve_limit(
        self,
        account_id: uuid.UUID,
        broker_id: str,
        kind: str,
    ) -> RiskLimit | None:
        """Walk account → broker → global; return first active hit."""
        for scope_type, scope_id in (
            ("account", str(account_id)),
            ("broker", broker_id),
            ("global", None),
        ):
            stmt = select(RiskLimit).where(
                RiskLimit.scope_type == scope_type,
                RiskLimit.limit_kind == kind,
                RiskLimit.is_active.is_(True),
            )
            stmt = (
                stmt.where(RiskLimit.scope_id.is_(None))
                if scope_id is None
                else stmt.where(RiskLimit.scope_id == scope_id)
            )
            row = (await self._db.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row
        return None

    async def evaluate(self, ctx: EvaluationContext, mode: EvalMode) -> GateVerdict:
        """Run all 7 checks; aggregate to GateVerdict.

        B1: skeleton returns ALLOW. B2-B7 add per-check methods. B8 wires
        the asyncio.gather aggregator + asymmetric margin policy.
        """
        t0 = time.perf_counter()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return GateVerdict(
            final_verdict="allow",
            blockers=[],
            warnings=[],
            latency_ms=latency_ms,
        )
