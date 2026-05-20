from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_VETO_WINDOW_S = 300  # 5-minute veto window


class AutoPromoteCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_sharpe: float
    max_drawdown: float
    min_win_rate: float
    min_comparison_days: int = 14
    auto_apply: bool = False


async def _expiry_promote(
    event_id: UUID,
    live_bot_id: UUID,
    shadow_bot_id: UUID,
    promoter: Any,
    telegram: Any,
    db_factory: Any,
) -> None:
    """Fire after veto window expires. CAS promote_pending -> success; promote if won."""
    async with db_factory() as db:
        result = cast(
            CursorResult,
            await db.execute(
                text(
                    "UPDATE shadow_promotion_events"
                    " SET status = 'success', promoted_at = now()"
                    " WHERE id = :eid AND status = 'promote_pending'"
                ),
                {"eid": event_id},
            ),
        )
        await db.commit()
        if result.rowcount == 0:
            log.info("auto_promote_expiry_skipped_vetoed", event_id=str(event_id))
            return

    try:
        async with db_factory() as db:
            await promoter.promote(live_bot_id, shadow_bot_id, "auto", db)
        m.orchestrator_auto_promote_total.labels(outcome="promoted").inc()
        await telegram.send(
            f"Auto-promoted shadow bot {shadow_bot_id} to live {live_bot_id} (veto window elapsed)"
        )
    except Exception:
        log.exception(
            "auto_promote_expiry_failed",
            live_bot_id=str(live_bot_id),
            shadow_bot_id=str(shadow_bot_id),
        )
        m.orchestrator_auto_promote_total.labels(outcome="error").inc()


async def handle_veto_token(token: UUID, db: AsyncSession) -> bool:
    """Mark a promote_pending event as vetoed. Returns True if token was valid and active."""
    now = datetime.now(UTC)
    result = cast(
        CursorResult,
        await db.execute(
            text(
                "UPDATE shadow_promotion_events"
                " SET status = 'vetoed'"
                " WHERE veto_token = :tok"
                "   AND status = 'promote_pending'"
                "   AND veto_expires_at > :now"
            ),
            {"tok": token, "now": now},
        ),
    )
    await db.commit()
    vetoed = result.rowcount > 0
    if vetoed:
        m.orchestrator_auto_promote_total.labels(outcome="vetoed").inc()
    return vetoed


async def recover_pending_veto_windows(
    db: AsyncSession,
    scheduler: Any,
    promoter: Any,
    telegram: Any,
    db_factory: Any,
) -> None:
    """Startup sweep: reschedule any promote_pending rows that have future expiry."""
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            text(
                "SELECT id, live_bot_id, shadow_bot_id, veto_expires_at"
                " FROM shadow_promotion_events"
                " WHERE status = 'promote_pending'"
                "   AND veto_expires_at > :now"
            ),
            {"now": now},
        )
    ).all()

    for event_id, live_bot_id, shadow_bot_id, expires_at in rows:
        run_date = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        scheduler.add_job(
            _expiry_promote,
            "date",
            run_date=run_date,
            kwargs={
                "event_id": event_id,
                "live_bot_id": live_bot_id,
                "shadow_bot_id": shadow_bot_id,
                "promoter": promoter,
                "telegram": telegram,
                "db_factory": db_factory,
            },
            id=f"veto_expiry_{event_id}",
            replace_existing=True,
        )
        log.info(
            "auto_promote_recovered_pending",
            event_id=str(event_id),
            expires_at=str(expires_at),
        )


class AutoPromoteEvaluator:
    def __init__(
        self,
        promoter_service: Any,
        telegram: Any,
        scheduler: Any | None = None,
        db_factory: Any | None = None,
    ) -> None:
        self._promoter = promoter_service
        self._telegram = telegram
        self._scheduler = scheduler
        self._db_factory = db_factory

    async def evaluate(self, live_bot_id: UUID, shadow_bot_id: UUID, db: AsyncSession) -> str:
        if not await self._master_switch_on(db):
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_master_switch_off"

        existing = (
            await db.execute(
                text(
                    "SELECT id FROM shadow_promotion_events"
                    " WHERE live_bot_id = :lid AND shadow_bot_id = :sid"
                    " AND status = 'success'"
                    " LIMIT 1"
                ),
                {"lid": live_bot_id, "sid": shadow_bot_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info(
                "auto_promote_already_promoted",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
            )
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_already_promoted"

        criteria_raw = (
            await db.execute(
                text("SELECT auto_promote_criteria FROM bots WHERE id = :lid LIMIT 1"),
                {"lid": live_bot_id},
            )
        ).scalar_one_or_none()
        if criteria_raw is None:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_no_criteria"
        criteria = AutoPromoteCriteria.model_validate_json(
            criteria_raw if isinstance(criteria_raw, str) else json.dumps(criteria_raw)
        )
        if not criteria.auto_apply:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_auto_apply_false"

        metrics_row = (
            await db.execute(
                text(
                    """
                    SELECT
                        avg(kpi_sharpe)    AS sharpe,
                        max(kpi_max_dd)    AS max_dd,
                        avg(kpi_win_rate)  AS win_rate,
                        avg(kpi_mar)       AS mar,
                        count(*)           AS trade_count,
                        :window_days       AS window_days
                    FROM bot_runs
                    WHERE bot_id = :sid
                      AND ended_at >= now() - :window_days * interval '1 day'
                    """
                ),
                {"sid": shadow_bot_id, "window_days": criteria.min_comparison_days},
            )
        ).all()

        if not metrics_row or metrics_row[0][4] is None or int(metrics_row[0][4]) == 0:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_insufficient_data"

        sharpe = float(metrics_row[0][0] or 0)
        max_dd = float(metrics_row[0][1] or 1)
        win_rate = float(metrics_row[0][2] or 0)

        if (
            sharpe < criteria.min_sharpe
            or max_dd > criteria.max_drawdown
            or win_rate < criteria.min_win_rate
        ):
            log.info(
                "auto_promote_criteria_not_met",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
                sharpe=sharpe,
                max_dd=max_dd,
                win_rate=win_rate,
            )
            m.orchestrator_auto_promote_total.labels(outcome="criteria_not_met").inc()
            return "criteria_not_met"

        # Check if already pending
        existing_pending = (
            await db.execute(
                text(
                    "SELECT id FROM shadow_promotion_events"
                    " WHERE live_bot_id = :lid AND shadow_bot_id = :sid"
                    " AND status = 'promote_pending'"
                    " LIMIT 1"
                ),
                {"lid": live_bot_id, "sid": shadow_bot_id},
            )
        ).scalar_one_or_none()
        if existing_pending is not None:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_already_pending"

        # Insert promote_pending row with veto token
        event_id = uuid.uuid4()
        veto_token = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=_VETO_WINDOW_S)
        await db.execute(
            text(
                "INSERT INTO shadow_promotion_events"
                " (id, live_bot_id, shadow_bot_id, status, veto_token, veto_expires_at)"
                " VALUES (:eid, :lid, :sid, 'promote_pending', :tok, :exp)"
            ),
            {
                "eid": event_id,
                "lid": live_bot_id,
                "sid": shadow_bot_id,
                "tok": veto_token,
                "exp": expires_at,
            },
        )
        await db.commit()

        await self._telegram.send(
            f"Auto-promote pending for shadow {shadow_bot_id} -> live {live_bot_id}.\n"
            f"Veto within {_VETO_WINDOW_S // 60}m: /veto_promote_{veto_token}\n"
            f"(Sharpe={sharpe:.2f}, MaxDD={max_dd:.1%}, WinRate={win_rate:.1%})"
        )

        if self._scheduler is not None:
            self._scheduler.add_job(
                _expiry_promote,
                "date",
                run_date=expires_at,
                kwargs={
                    "event_id": event_id,
                    "live_bot_id": live_bot_id,
                    "shadow_bot_id": shadow_bot_id,
                    "promoter": self._promoter,
                    "telegram": self._telegram,
                    "db_factory": self._db_factory,
                },
                id=f"veto_expiry_{event_id}",
                replace_existing=True,
            )

        m.orchestrator_auto_promote_total.labels(outcome="pending_veto_window").inc()
        return "pending_veto_window"

    async def _master_switch_on(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='auto_promote_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        return json.loads(row) is not False and json.loads(row) != "false"
