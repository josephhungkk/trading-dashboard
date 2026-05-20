from __future__ import annotations

import json
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

    async def check(
        self,
        account_id: UUID,
        instrument_id: int,
        qty: Decimal,
        price: Decimal,
        currency: str,
        db: AsyncSession,
        multiplier: Decimal = Decimal("1"),
    ) -> ExposureOutcome:
        t0 = time.perf_counter()
        try:
            if not await self._gate_enabled(db):
                return ExposureOutcome.ALLOW

            fx = await get_fx_rate(currency, self._redis)
            order_notional = qty * price * multiplier * fx

            exposure = await self._read_exposure(account_id, instrument_id, db)
            limits = await self._fetch_limits(account_id, instrument_id, db)

            outcome = ExposureOutcome.ALLOW
            for _limit_id, limit_type, instr_id, max_notional, _currency, enabled in limits:
                if not enabled:
                    continue
                if limit_type == "total_notional":
                    projected = exposure["total"] + order_notional
                    if projected > max_notional:
                        outcome = ExposureOutcome.BLOCK
                        break
                elif limit_type == "per_instrument" and instr_id == instrument_id:
                    instr_key = f"instr:{instrument_id}"
                    projected = exposure.get(instr_key, Decimal("0")) + order_notional
                    if projected > max_notional:
                        outcome = ExposureOutcome.BLOCK
                        break

            label = outcome.value
            m.orchestrator_exposure_checks_total.labels(
                outcome=label,
                limit_type="total_notional",
            ).inc()
            m.orchestrator_exposure_gate_latency_seconds.observe(time.perf_counter() - t0)
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
        row = (
            await db.execute(
                text(
                    "SELECT COALESCE(SUM(ABS(notional_usd)), 0)::numeric"
                    " FROM bot_orders"
                    " WHERE account_id = :acct"
                    "   AND status NOT IN ('cancelled', 'rejected')"
                ),
                {"acct": account_id},
            )
        ).scalar_one_or_none()
        total = Decimal(str(row)) if row is not None else Decimal("0")
        exposure: dict[str, Decimal] = {"total": total}
        try:
            await self._redis.hset(redis_key, mapping={"total": str(total)})
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
                "SELECT id, limit_type, instrument_id, max_notional, currency, enabled"
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
    ) -> None:
        """Atomically update exposure HASH on order fill. Call from BotFillRouter."""
        redis_key = f"portfolio:exposure:{account_id}"
        instr_key = f"instr:{instrument_id}"
        try:
            sha = await self._ensure_lua_loaded()
            await self._redis.evalsha(
                sha,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                str(signed_delta_usd),
            )
        except Exception:
            await self._redis.eval(
                EXPOSURE_UPDATE_SCRIPT,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                str(signed_delta_usd),
            )
