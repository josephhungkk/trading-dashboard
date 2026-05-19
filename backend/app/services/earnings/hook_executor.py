from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core import metrics
from app.services.orders_service import place_order_internal

log = structlog.get_logger()

_monitor_tasks: set[asyncio.Task[None]] = set()
_hook_tasks: set[asyncio.Task[None]] = set()


class HookExecutor:
    def __init__(
        self,
        *,
        db_factory: Any,
        redis: Any,
        cfg: Any = None,
        registry: Any = None,
        capability: Any = None,
    ) -> None:
        self.db_factory = db_factory
        self.redis = redis
        self.cfg = cfg
        self.registry = registry
        self.capability = capability

    def _resolve_flat_side(self, asset_class: str, qty: float) -> str:
        if asset_class.upper() == "OPTION":
            return "sell_to_close" if qty > 0 else "buy_to_close"
        return "sell" if qty > 0 else "buy"

    async def _resolve_open_position(self, db: Any, instrument_id: int, account_id: UUID) -> float:
        result = await db.execute(
            text(
                """
                SELECT COALESCE(SUM(qty), 0)
                  FROM positions
                 WHERE instrument_id = :instrument_id
                   AND account_id = :account_id
                """
            ),
            {"instrument_id": instrument_id, "account_id": account_id},
        )
        return float(result.scalar_one() or 0)

    async def _claim_redis(self, redis: Any, hook_id: UUID, event_id: UUID) -> bool:
        key = f"earnings:hook-claim:{hook_id}:{event_id}"
        return bool(await redis.set(key, "1", ex=7 * 24 * 60 * 60, nx=True))

    async def _check_audit_exists(self, db: Any, hook_id: UUID, event_id: UUID) -> bool:
        result = await db.execute(
            text("SELECT 1 FROM hook_audit WHERE hook_id = :hook_id AND event_id = :event_id"),
            {"hook_id": hook_id, "event_id": event_id},
        )
        return result.scalar_one_or_none() is not None

    async def _insert_audit_claim(self, db: Any, hook_id: UUID, event_id: UUID) -> UUID | None:
        try:
            result = await db.execute(
                text(
                    """
                    INSERT INTO hook_audit (hook_id, event_id, outcome)
                    VALUES (:hook_id, :event_id, 'failed')
                    RETURNING id
                    """
                ),
                {"hook_id": hook_id, "event_id": event_id},
            )
            audit_id = result.scalar_one()
            await db.commit()
            return audit_id
        except IntegrityError:
            await db.rollback()
            return None

    async def _update_audit(
        self,
        db: Any,
        audit_id: UUID,
        *,
        outcome: str,
        order_id: UUID | None = None,
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE hook_audit
                   SET outcome = :outcome,
                       order_id = :order_id
                 WHERE id = :audit_id
                """
            ),
            {"audit_id": audit_id, "outcome": outcome, "order_id": order_id},
        )
        await db.commit()

    async def _asset_class(self, db: Any, instrument_id: int) -> str:
        result = await db.execute(
            text("SELECT asset_class::text FROM instruments WHERE id = :instrument_id"),
            {"instrument_id": instrument_id},
        )
        return str(result.scalar_one_or_none() or "STOCK")

    async def fire_auto_flat(self, hook: dict[str, Any], event: dict[str, Any]) -> None:
        hook_id = hook["id"]
        event_id = event["id"]
        hook_type = "auto_flat"
        async with self.db_factory() as db:
            try:
                qty = await self._resolve_open_position(
                    db, hook["instrument_id"], hook["account_id"]
                )
                if await self._check_audit_exists(db, hook_id, event_id):
                    metrics.earnings_dedup_skips_total.labels(source="hook_audit").inc()
                    return
                if not await self._claim_redis(self.redis, hook_id, event_id):
                    metrics.earnings_dedup_skips_total.labels(source="redis").inc()
                    return
                audit_id = await self._insert_audit_claim(db, hook_id, event_id)
                if audit_id is None:
                    metrics.earnings_dedup_skips_total.labels(source="hook_audit").inc()
                    return
                if qty == 0:
                    await self._update_audit(db, audit_id, outcome="skipped_no_position")
                    metrics.earnings_hooks_fired_total.labels(hook_type=hook_type).inc()
                    return

                asset_class = await self._asset_class(db, hook["instrument_id"])
                side = self._resolve_flat_side(asset_class, qty)
                response = await place_order_internal(
                    cfg=self.cfg,
                    db=db,
                    redis=self.redis,
                    registry=self.registry,
                    capability=self.capability,
                    jwt_subject=hook["jwt_subject"],
                    issuer="earnings_hook",
                    account_id=hook["account_id"],
                    instrument_id=hook["instrument_id"],
                    side=side,
                    qty=str(abs(Decimal(str(qty)))),
                    order_type="MARKET",
                    position_effect="close",
                    bypass_pdt_when_closing=True,
                    client_order_id=hook.get("client_order_id") or uuid4(),
                )
                await self._update_audit(db, audit_id, outcome="placed", order_id=response.id)
                metrics.earnings_autoflat_qty_total.inc(abs(qty))
                metrics.earnings_hooks_fired_total.labels(hook_type=hook_type).inc()
            except Exception:
                metrics.earnings_hooks_failed_total.labels(hook_type=hook_type).inc()
                log.exception(
                    "earnings_auto_flat_failed",
                    hook_id=str(hook_id),
                    event_id=str(event_id),
                    instrument_id=hook.get("instrument_id"),
                    account_id=str(hook.get("account_id")),
                    side=hook.get("hook_type"),
                )

    async def fire_auto_pause_bot(self, hook: dict[str, Any], event: dict[str, Any]) -> None:
        log.info("earnings_auto_pause_bot_stub", hook_id=str(hook["id"]), event_id=str(event["id"]))
        metrics.earnings_hooks_fired_total.labels(hook_type="auto_pause_bot").inc()

    async def evaluate_hooks(self) -> None:
        async with self.db_factory() as db:
            rows = await db.execute(
                text(
                    """
                    SELECT h.id AS hook_id,
                           h.instrument_id,
                           h.account_id,
                           h.jwt_subject,
                           h.hook_type,
                           h.minutes_before,
                           h.bot_id,
                           e.id AS event_id,
                           e.announced_at,
                           e.announced_date,
                           e.time_of_day
                      FROM earnings_hooks h
                      JOIN earnings_events e ON e.instrument_id = h.instrument_id
                     WHERE h.enabled = true
                       AND COALESCE(
                             e.announced_at,
                             e.announced_date::timestamp AT TIME ZONE 'UTC'
                           ) BETWEEN now() AND now() + (h.minutes_before * interval '1 minute')
                    """
                )
            )
            hook_rows = rows.mappings().all()

        for row in hook_rows:
            hook = {
                "id": row["hook_id"],
                "instrument_id": row["instrument_id"],
                "account_id": row["account_id"],
                "jwt_subject": row["jwt_subject"],
                "hook_type": row["hook_type"],
                "minutes_before": row["minutes_before"],
                "bot_id": row["bot_id"],
            }
            event = {
                "id": row["event_id"],
                "announced_at": row["announced_at"],
                "announced_date": row["announced_date"],
                "time_of_day": row["time_of_day"],
            }
            if row["hook_type"] == "auto_flat":
                task = asyncio.create_task(self.fire_auto_flat(hook, event))
            else:
                task = asyncio.create_task(self.fire_auto_pause_bot(hook, event))
            _hook_tasks.add(task)
            task.add_done_callback(_hook_tasks.discard)
