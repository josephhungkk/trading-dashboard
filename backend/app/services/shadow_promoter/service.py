from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.shadow_promoter import metrics as m
from app.services.shadow_promoter.types import (
    ShadowComparisonReport,
    ShadowMetrics,
    ShadowVsLive,
)

logger = structlog.get_logger(__name__)


class ShadowPromoterService:
    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        supervisor: Any,
        redis: Any,
    ) -> None:
        self.db_factory = db_factory
        self.supervisor = supervisor
        self.redis = redis

    async def create_shadow(
        self,
        live_bot_id: UUID,
        override_params: dict[str, Any],
        comparison_window_days: int,
        created_by: str,
        db: AsyncSession,
    ) -> UUID:
        live_result = await db.execute(
            text("SELECT * FROM bots WHERE id=:id AND deleted_at IS NULL"),
            {"id": live_bot_id},
        )
        live = live_result.mappings().first()
        if live is None:
            raise ValueError("bot_not_found")
        if live["is_shadow"] is True:
            raise ValueError("cannot_shadow_a_shadow")

        live_strategy_params = self._as_dict(live.get("strategy_params"))
        merged_params = {**live_strategy_params, **override_params}
        shadow_result = await db.execute(
            text(
                """
                INSERT INTO bots (
                    name, strategy_file, strategy_params, mode, status, is_shadow,
                    shadow_of, shadow_comparison_window_days, advisor_config,
                    strategy_schema
                )
                VALUES (
                    :name, :strategy_file, CAST(:strategy_params AS jsonb), 'paper',
                    'stopped', true, :shadow_of, :window_days,
                    CAST(:advisor_config AS jsonb), CAST(:strategy_schema AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "name": f"{live['name']} [shadow]",
                "strategy_file": live["strategy_file"],
                "strategy_params": json.dumps(merged_params),
                "shadow_of": live_bot_id,
                "window_days": comparison_window_days,
                "advisor_config": json.dumps(self._as_dict(live.get("advisor_config"))),
                "strategy_schema": json.dumps(self._as_dict(live.get("strategy_schema"))),
            },
        )
        shadow_id: UUID = UUID(str(shadow_result.scalar_one()))

        caps_result = await db.execute(
            text("SELECT * FROM bot_risk_caps WHERE bot_id=:id"),
            {"id": live_bot_id},
        )
        caps = caps_result.mappings().first()
        if caps is not None:
            caps_values = dict(caps)
            caps_values.pop("id", None)
            caps_values["bot_id"] = shadow_id
            await self._insert_mapping(db, "bot_risk_caps", caps_values)

        accounts_result = await db.execute(
            text("SELECT * FROM bot_accounts WHERE bot_id=:id AND deleted_at IS NULL"),
            {"id": live_bot_id},
        )
        for account in accounts_result.mappings().all():
            account_values = dict(account)
            account_values.pop("id", None)
            account_values["bot_id"] = shadow_id
            await self._insert_mapping(db, "bot_accounts", account_values)

        await db.commit()
        m.shadow_promoter_created_total.inc()
        return shadow_id

    async def get_comparison(
        self,
        live_bot_id: UUID,
        db: AsyncSession,
    ) -> ShadowComparisonReport:
        shadow_result = await db.execute(
            text(
                """
                SELECT * FROM bots
                WHERE shadow_of=:live_bot_id AND is_shadow=true AND deleted_at IS NULL
                """
            ),
            {"live_bot_id": live_bot_id},
        )
        shadows: list[ShadowVsLive] = []
        for shadow in shadow_result.mappings().all():
            window_days = int(shadow["shadow_comparison_window_days"])
            shadow_metrics = await self._aggregate_metrics(shadow["id"], window_days, db)
            live_metrics = await self._aggregate_metrics(live_bot_id, window_days, db)
            ready_result = await db.execute(
                text(
                    """
                    SELECT MIN(started_at)
                    FROM bot_runs
                    WHERE bot_id=:shadow_id AND status='stopped'
                    """
                ),
                {"shadow_id": shadow["id"]},
            )
            first_stopped_at = ready_result.scalar_one_or_none()
            comparison_ready = first_stopped_at is not None and first_stopped_at <= datetime.now(
                UTC
            ) - timedelta(days=window_days)
            delta = {
                "sharpe": f"{shadow_metrics.sharpe - live_metrics.sharpe:+.2f}",
                "max_dd": f"{shadow_metrics.max_dd - live_metrics.max_dd:+.2f}",
            }
            shadows.append(
                ShadowVsLive(
                    shadow_bot_id=shadow["id"],
                    shadow_metrics=shadow_metrics,
                    live_metrics=live_metrics,
                    delta=delta,
                    comparison_ready=comparison_ready,
                )
            )
        return ShadowComparisonReport(
            live_bot_id=live_bot_id,
            shadows=shadows,
            generated_at=datetime.now(UTC),
        )

    async def promote(
        self,
        live_bot_id: UUID,
        shadow_bot_id: UUID,
        promoted_by: str,
        db: AsyncSession,
    ) -> None:
        try:
            shadow_result = await db.execute(
                text("SELECT * FROM bots WHERE id=:id AND deleted_at IS NULL"),
                {"id": shadow_bot_id},
            )
            shadow = shadow_result.mappings().first()
            if shadow is None:
                raise ValueError("shadow_not_found")
            if shadow["shadow_of"] != live_bot_id:
                raise ValueError("shadow_not_owned_by_live_bot")
            if not shadow["is_shadow"]:
                raise ValueError("bot_is_not_a_shadow")

            for bot_id in [live_bot_id, shadow_bot_id]:
                await self.redis.publish(
                    f"bot:control:{bot_id}",
                    json.dumps({"command": "STOP"}),
                )

            await db.execute(
                text(
                    """
                    UPDATE bots
                    SET strategy_params=CAST(:params AS jsonb), shadow_promoted_at=now()
                    WHERE id=:live_bot_id
                    """
                ),
                {
                    "params": json.dumps(self._as_dict(shadow.get("strategy_params"))),
                    "live_bot_id": live_bot_id,
                },
            )

            window_days = int(shadow["shadow_comparison_window_days"])
            shadow_metrics = await self._aggregate_metrics(shadow_bot_id, window_days, db)
            live_metrics = await self._aggregate_metrics(live_bot_id, window_days, db)
            await db.execute(
                text(
                    """
                    INSERT INTO shadow_promotion_events (
                        shadow_bot_id, live_bot_id, promoted_by,
                        comparison_window_days, comparison_window_start,
                        shadow_metrics, live_metrics
                    )
                    VALUES (
                        :shadow_bot_id, :live_bot_id, :promoted_by,
                        :comparison_window_days,
                        now() - :comparison_window_days * interval '1 day',
                        :shadow_metrics::jsonb, :live_metrics::jsonb
                    )
                    """
                ),
                {
                    "shadow_bot_id": shadow_bot_id,
                    "live_bot_id": live_bot_id,
                    "promoted_by": promoted_by,
                    "comparison_window_days": window_days,
                    "shadow_metrics": json.dumps(shadow_metrics.model_dump()),
                    "live_metrics": json.dumps(live_metrics.model_dump()),
                },
            )
            await db.execute(
                text("UPDATE bots SET deleted_at=now(), is_shadow=false WHERE id=:shadow_bot_id"),
                {"shadow_bot_id": shadow_bot_id},
            )
            await db.commit()
            m.shadow_promoter_promoted_total.inc()
        except Exception:
            logger.exception(
                "shadow_promoter_promote_failed",
                live_bot_id=str(live_bot_id),
                shadow_bot_id=str(shadow_bot_id),
            )
            m.shadow_promoter_promote_failures_total.inc()
            raise

    async def check_auto_promote_eligibility(self, live_bot_id: UUID, db: AsyncSession) -> bool:
        return False

    async def _aggregate_metrics(
        self,
        bot_id: UUID,
        window_days: int,
        db: AsyncSession,
    ) -> ShadowMetrics:
        result = await db.execute(
            text(
                """
                SELECT
                    avg(kpi_sharpe) AS sharpe,
                    avg(kpi_mar) AS mar,
                    avg(kpi_max_dd) AS max_dd,
                    avg(kpi_win_rate) AS win_rate,
                    avg(kpi_avg_trade_pnl) AS avg_trade_pnl,
                    count(*) AS total_trades
                FROM bot_runs
                WHERE bot_id=:bid
                  AND status='stopped'
                  AND started_at >= now() - :window_days * interval '1 day'
                """
            ),
            {"bid": bot_id, "window_days": window_days},
        )
        row = result.mappings().one()
        return ShadowMetrics(
            sharpe=float(row["sharpe"] or 0),
            mar=float(row["mar"] or 0),
            max_dd=float(row["max_dd"] or 0),
            win_rate=float(row["win_rate"] or 0),
            avg_trade_pnl=float(row["avg_trade_pnl"] or 0),
            total_trades=int(row["total_trades"] or 0),
            window_days=window_days,
        )

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed: dict[str, Any] = json.loads(value)
            return parsed
        return dict(value)

    @staticmethod
    async def _insert_mapping(db: AsyncSession, table_name: str, values: dict[str, Any]) -> None:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        await db.execute(
            text(f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"),
            values,
        )
