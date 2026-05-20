from __future__ import annotations

import json
import math
import time
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fx import get_fx_rate
from app.services.orchestrator import metrics as m
from app.services.orchestrator.exposure_gate_lua import EXPOSURE_UPDATE_SCRIPT

log = structlog.get_logger()


class ExposureOutcome(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PortfolioExposureGate:
    """Pre-trade station 5.75 -- portfolio-level notional exposure check.

    Redis HASH portfolio:exposure:{account_id}:
      total                   -> total USD notional
      instr:{instrument_id}   -> per-instrument USD notional
      sector:{sector}         -> per-sector USD notional
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._lua_sha: str | None = None

    async def _ensure_lua_loaded(self) -> str:
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(EXPOSURE_UPDATE_SCRIPT)
        return self._lua_sha

    async def _gate_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='exposure_gate_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return True
        if isinstance(row, bool):
            return row
        if isinstance(row, bytes):
            return json.loads(row.decode()) is not False
        if isinstance(row, str):
            return json.loads(row) is not False
        return True

    async def _mv_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value FROM app_config"
                    " WHERE namespace='orchestrator' AND key='marginal_variance_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        val = row.decode() if isinstance(row, bytes) else str(row)
        return json.loads(val) is not False

    async def _compute_mv_notional(
        self,
        account_id: UUID,
        instrument_id: int,
        order_notional: Decimal,
        exposure: dict[str, Decimal],
    ) -> Decimal | None:
        """Return MV-adjusted effective notional, or None if matrix unavailable."""
        raw = await self._redis.get(f"portfolio:correlation:{account_id}")
        if raw is None:
            m.orchestrator_marginal_variance_fallback_total.labels(reason="no_matrix").inc()
            return None
        try:
            matrix: dict[str, dict[str, float]] = json.loads(
                raw.decode() if isinstance(raw, bytes) else raw
            )
        except json.JSONDecodeError, ValueError:
            m.orchestrator_marginal_variance_fallback_total.labels(reason="bad_matrix").inc()
            return None

        if not matrix or str(instrument_id) not in matrix:
            m.orchestrator_marginal_variance_fallback_total.labels(
                reason="missing_instrument"
            ).inc()
            return None

        # corr_sum = sum_i (w_i / w_new) * rho(i, new)
        # effective = raw * sqrt(max(1 + 2 * corr_sum, 0))
        new_col = matrix[str(instrument_id)]
        corr_sum = 0.0
        for key, w_i in exposure.items():
            if not key.startswith("instr:"):
                continue
            iid_str = key[len("instr:") :]
            rho = new_col.get(iid_str, 0.0)
            if order_notional != 0:
                corr_sum += float(w_i / order_notional) * rho

        factor = math.sqrt(max(1.0 + 2.0 * corr_sum, 0.0))
        return order_notional * Decimal(str(factor))

    async def check(
        self,
        account_id: UUID,
        instrument_id: int,
        qty: Decimal,
        price: Decimal,
        currency: str,
        db: AsyncSession,
        multiplier: Decimal = Decimal("1"),
        instrument_sector: str | None = None,
    ) -> ExposureOutcome:
        t0 = time.perf_counter()
        path = "raw"
        try:
            if not await self._gate_enabled(db):
                return ExposureOutcome.ALLOW

            fx = await get_fx_rate(currency, self._redis)
            order_notional = qty * price * multiplier * fx

            exposure = await self._read_exposure(account_id, instrument_id, db)
            use_mv = await self._mv_enabled(db)
            limits = await self._fetch_limits(account_id, instrument_id, db)

            # Compute effective notional (MV-adjusted or raw)
            effective_notional = order_notional
            if use_mv:
                mv = await self._compute_mv_notional(
                    account_id=account_id,
                    instrument_id=instrument_id,
                    order_notional=order_notional,
                    exposure=exposure,
                )
                if mv is not None:
                    effective_notional = mv
                    path = "mv"

            outcome = ExposureOutcome.ALLOW
            triggered_limit_type = "none"
            for row in limits:
                _limit_id, limit_type, instr_id, max_notional, _currency, enabled, sector = row
                if not enabled:
                    continue
                if limit_type == "total_notional":
                    projected = exposure["total"] + effective_notional
                elif limit_type == "per_instrument" and instr_id == instrument_id:
                    instr_key = f"instr:{instrument_id}"
                    projected = exposure.get(instr_key, Decimal("0")) + effective_notional
                elif (
                    limit_type == "per_sector"
                    and sector is not None
                    and instrument_sector is not None
                    and sector == instrument_sector
                ):
                    sector_key = f"sector:{sector}"
                    projected = exposure.get(sector_key, Decimal("0")) + order_notional
                else:
                    continue
                warn_threshold = max_notional * Decimal("0.8")
                if projected > max_notional:
                    outcome = ExposureOutcome.BLOCK
                    triggered_limit_type = limit_type
                    break
                if projected > warn_threshold and outcome == ExposureOutcome.ALLOW:
                    outcome = ExposureOutcome.WARN
                    triggered_limit_type = limit_type

            m.orchestrator_exposure_checks_total.labels(
                outcome=outcome.value,
                limit_type=triggered_limit_type,
            ).inc()
            m.orchestrator_exposure_gate_latency_seconds.labels(path=path).observe(
                time.perf_counter() - t0
            )
            return outcome

        except SQLAlchemyError:
            m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="block").inc()
            log.exception("exposure_gate_pg_unavailable", account_id=str(account_id))
            return ExposureOutcome.BLOCK

    async def _read_exposure(
        self,
        account_id: UUID,
        instrument_id: int,
        db: AsyncSession,
    ) -> dict[str, Decimal]:
        redis_key = f"portfolio:exposure:{account_id}"
        raw = await self._redis.hgetall(redis_key)
        if raw:
            return {
                k.decode() if isinstance(k, bytes) else k: Decimal(
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw.items()
            }

        m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="used").inc()
        rows = (
            await db.execute(
                text(
                    "SELECT instrument_id, COALESCE(SUM(ABS(notional_usd)), 0)::numeric"
                    " FROM bot_orders"
                    " WHERE account_id = :acct"
                    "   AND status NOT IN ('cancelled', 'rejected')"
                    " GROUP BY instrument_id"
                ),
                {"acct": account_id},
            )
        ).all()
        total = Decimal("0")
        per_instr: dict[str, Decimal] = {}
        for iid, notional in rows:
            val = Decimal(str(notional)) if notional is not None else Decimal("0")
            total += val
            if iid is not None:
                per_instr[f"instr:{iid}"] = val
        exposure: dict[str, Decimal] = {"total": total, **per_instr}
        try:
            mapping = {"total": str(total)}
            mapping.update({k: str(v) for k, v in per_instr.items()})
            await self._redis.hset(redis_key, mapping=mapping)
            await self._redis.expire(redis_key, 3600)
        except Exception:
            pass
        return exposure

    async def _fetch_limits(
        self,
        account_id: UUID,
        instrument_id: int,
        db: AsyncSession,
    ) -> list[tuple[Any, ...]]:
        result = await db.execute(
            text(
                "SELECT id, limit_type, instrument_id, max_notional, currency, enabled, sector"
                " FROM portfolio_exposure_limits"
                " WHERE account_id = :acct AND enabled = true"
                "   AND (instrument_id IS NULL OR instrument_id = :iid)"
            ),
            {"acct": account_id, "iid": instrument_id},
        )
        return [tuple(row) for row in result.all()]

    async def update_on_fill(
        self,
        account_id: UUID,
        instrument_id: int,
        signed_delta_usd: Decimal,
        sector: str | None = None,
    ) -> None:
        """Atomically update exposure HASH on order fill. Call from BotFillRouter."""
        redis_key = f"portfolio:exposure:{account_id}"
        instr_key = f"instr:{instrument_id}"
        sector_key = f"sector:{sector}" if sector else ""
        try:
            sha = await self._ensure_lua_loaded()
            await self._redis.evalsha(
                sha,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                sector_key,
            )
        except Exception:
            await self._redis.eval(
                EXPOSURE_UPDATE_SCRIPT,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                sector_key,
            )
