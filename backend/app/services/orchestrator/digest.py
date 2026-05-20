from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()


class HealthDigestService:
    """APScheduler job: compute per-bot health snapshots and post Telegram digest.

    Cron: 0 3 * * * (03:00 UTC). max_instances=1, coalesce=True.
    """

    def __init__(self, db_factory: Any, telegram: Any, redis: Any) -> None:
        self._db_factory = db_factory
        self._telegram = telegram
        self._redis = redis

    async def run(self) -> None:
        t0 = time.perf_counter()
        log.info("orchestrator_digest_start")
        processed = 0
        errors = 0

        async with self._db_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, name FROM bots"
                        " WHERE deleted_at IS NULL AND is_shadow = false"
                        " AND status NOT IN ('stopped', 'error', 'vetoed')"
                    )
                )
            ).all()

        bot_stats: list[dict] = []
        for bot_id, bot_name in rows:
            try:
                async with self._db_factory() as bot_db:
                    stats = await self._compute_bot_stats(bot_db, bot_id, bot_name)
                    await self._insert_snapshot(bot_db, bot_id, stats)
                    bot_stats.append(stats)
                    processed += 1
            except Exception:
                errors += 1
                log.exception("orchestrator_digest_bot_error", bot_id=str(bot_id))

        await self._send_telegram_digest(bot_stats)
        elapsed = time.perf_counter() - t0
        log.info(
            "orchestrator_digest_complete",
            processed=processed,
            errors=errors,
            elapsed_s=round(elapsed, 3),
        )
        m.orchestrator_digest_runs_total.inc()

    async def _compute_bot_stats(self, db: AsyncSession, bot_id: UUID, bot_name: str) -> dict:
        def _scalar(row: Any) -> Any:
            return row[0] if row is not None else None

        sharpe_30d = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT AVG(kpi_sharpe) FROM bot_runs"
                        " WHERE bot_id = :bid AND started_at >= NOW() - INTERVAL '30 days'"
                        " AND kpi_sharpe IS NOT NULL"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        sharpe_7d = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT AVG(kpi_sharpe) FROM bot_runs"
                        " WHERE bot_id = :bid AND started_at >= NOW() - INTERVAL '7 days'"
                        " AND kpi_sharpe IS NOT NULL"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        max_drawdown = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT MAX(kpi_max_dd) FROM bot_runs"
                        " WHERE bot_id = :bid AND started_at >= NOW() - INTERVAL '30 days'"
                        " AND kpi_max_dd IS NOT NULL"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        win_rate = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT AVG(kpi_win_rate) FROM bot_runs"
                        " WHERE bot_id = :bid AND started_at >= NOW() - INTERVAL '30 days'"
                        " AND kpi_win_rate IS NOT NULL"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        trade_count = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM bot_runs"
                        " WHERE bot_id = :bid AND started_at >= NOW() - INTERVAL '30 days'"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        advisor_accuracy = _scalar(
            (
                await db.execute(
                    text(
                        "SELECT AVG(CASE WHEN outcome_1h_correct THEN 1.0 ELSE 0.0 END)"
                        " FROM bot_advisor_decisions"
                        " WHERE bot_id = :bid AND created_at >= NOW() - INTERVAL '30 days'"
                        " AND outcome_1h_correct IS NOT NULL"
                    ),
                    {"bid": bot_id},
                )
            ).fetchone()
        )
        return {
            "bot_id": bot_id,
            "bot_name": bot_name,
            "sharpe_30d": float(sharpe_30d) if sharpe_30d is not None else None,
            "sharpe_7d": float(sharpe_7d) if sharpe_7d is not None else None,
            "max_drawdown": float(max_drawdown) if max_drawdown is not None else None,
            "win_rate": float(win_rate) if win_rate is not None else None,
            "total_pnl": None,
            "trade_count": int(trade_count) if trade_count is not None else 0,
            "advisor_veto_accuracy_1h": (
                float(advisor_accuracy) if advisor_accuracy is not None else None
            ),
            "exposure_utilisation": None,
        }

    async def _insert_snapshot(self, db: AsyncSession, bot_id: UUID, stats: dict) -> None:
        await db.execute(
            text(
                "INSERT INTO bot_health_snapshots"
                " (bot_id, snapshot_at, sharpe_30d, sharpe_7d, max_drawdown,"
                "  win_rate, total_pnl, trade_count,"
                "  advisor_veto_accuracy_1h, exposure_utilisation)"
                " VALUES"
                " (:bot_id, NOW(), :sharpe_30d, :sharpe_7d, :max_drawdown,"
                "  :win_rate, :total_pnl, :trade_count,"
                "  :advisor_veto_accuracy_1h, :exposure_utilisation)"
                " ON CONFLICT (bot_id, snapshot_at) DO NOTHING"
            ),
            {
                "bot_id": bot_id,
                "sharpe_30d": stats["sharpe_30d"],
                "sharpe_7d": stats["sharpe_7d"],
                "max_drawdown": stats["max_drawdown"],
                "win_rate": stats["win_rate"],
                "total_pnl": stats["total_pnl"],
                "trade_count": stats["trade_count"],
                "advisor_veto_accuracy_1h": stats["advisor_veto_accuracy_1h"],
                "exposure_utilisation": stats["exposure_utilisation"],
            },
        )
        await db.commit()

    async def _send_telegram_digest(self, bot_stats: list[dict]) -> None:
        if self._telegram is None:
            return
        try:
            async with self._db_factory() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT value FROM app_config"
                            " WHERE namespace = 'orchestrator' AND key = 'digest_telegram_enabled'"
                        )
                    )
                ).fetchone()
                enabled = True
                if row is not None:
                    enabled = str(row[0]).lower() not in ("false", "0", "no")
                if not enabled:
                    return

            from app.services.orchestrator.digest_telegram import send_digest

            underperform_threshold = 0.0
            try:
                async with self._db_factory() as db2:
                    thresh_row = (
                        await db2.execute(
                            text(
                                "SELECT value FROM app_config"
                                " WHERE namespace = 'orchestrator'"
                                " AND key = 'underperform_sharpe_threshold'"
                            )
                        )
                    ).fetchone()
                    if thresh_row is not None:
                        underperform_threshold = float(thresh_row[0])
            except Exception:
                pass

            await send_digest(
                self._telegram,
                bot_stats,
                underperform_threshold=underperform_threshold,
            )
        except Exception:
            log.exception("orchestrator_digest_telegram_error")
