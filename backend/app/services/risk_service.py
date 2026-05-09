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
from typing import Any, Literal, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.risk import AccountKillSwitch, RiskLimit
from app.schemas.risk import GateBlockerEntry, GateVerdict, GateWarningEntry

CheckResult = tuple[GateBlockerEntry | None, GateWarningEntry | None] | None


class _ConfigProto(Protocol):
    """Minimal protocol for the ConfigService dependency this service needs."""

    async def get_bool(self, namespace: str, key: str, *, default: bool = False) -> bool: ...


class _RedisProto(Protocol):
    """Minimal protocol for the Redis dependency this service needs."""

    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, *, ex: int | None = None) -> Any: ...
    async def decr(self, key: str) -> Any: ...
    async def incr(self, key: str) -> Any: ...
    async def incrbyfloat(self, key: str, amount: float) -> Any: ...


class _SidecarProto(Protocol):
    """Minimal protocol for the broker-client dependency this service needs."""

    async def preview_order(self, **kwargs: Any) -> Any: ...
    async def get_account_summary(self, account_id: uuid.UUID) -> Any: ...


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
        redis: _RedisProto,
        config: _ConfigProto,
        sidecar: _SidecarProto,
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

    async def _check_account_kill_switch(self, ctx: EvaluationContext) -> CheckResult:
        """B2: BLOCK when account_kill_switches.is_enabled=True for the account."""
        stmt = select(AccountKillSwitch).where(AccountKillSwitch.account_id == ctx.account_id)
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        if row is None or not row.is_enabled:
            return None
        return (
            GateBlockerEntry(
                check="account_kill_switch",
                message=f"account kill switch enabled — reason: {row.reason}",
                code="account_kill_switch_enabled",
            ),
            None,
        )

    async def _check_broker_kill_switch(self, ctx: EvaluationContext) -> CheckResult:
        """B2: composes Phase 5b H0 (app_config.broker.kill_switch_enabled)."""
        is_on = await self._config.get_bool("broker", "kill_switch_enabled", default=False)
        if not is_on:
            return None
        return (
            GateBlockerEntry(
                check="broker_kill_switch",
                message=f"broker {ctx.broker_id} kill switch enabled (Phase 5b H0)",
                code="broker_kill_switch_enabled",
            ),
            None,
        )

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
