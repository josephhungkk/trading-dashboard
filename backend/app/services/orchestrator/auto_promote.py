from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()


class AutoPromoteCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_sharpe: float
    max_drawdown: float
    min_win_rate: float
    min_comparison_days: int = 14
    auto_apply: bool = False


class AutoPromoteEvaluator:
    def __init__(self, promoter_service: Any, telegram: Any) -> None:
        self._promoter = promoter_service
        self._telegram = telegram

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
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "criteria_not_met"

        try:
            await self._promoter.promote(live_bot_id, shadow_bot_id, "auto", db)
            m.orchestrator_auto_promote_total.labels(outcome="promoted").inc()
            await self._telegram.send(
                f"Auto-promoted shadow bot {shadow_bot_id} to live {live_bot_id}"
                f" (Sharpe={sharpe:.2f}, MaxDD={max_dd:.1%}, WinRate={win_rate:.1%})"
            )
            return "promoted"
        except Exception:
            log.exception(
                "auto_promote_failed",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
            )
            m.orchestrator_auto_promote_total.labels(outcome="error").inc()
            return "error"

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
