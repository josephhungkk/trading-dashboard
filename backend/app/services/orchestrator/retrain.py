from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from app.services.orchestrator import metrics as m

log = structlog.get_logger()


class NightlyRetrainJob:
    """APScheduler job: parallel ParamTunerService.trigger across all running bots.

    Cron: 0 2 * * * (02:00 UTC). max_instances=1, coalesce=True.
    Semaphore N = max_parallel (default 2).
    """

    def __init__(
        self,
        db_factory: Any,
        param_tuner_factory: Callable[..., Any],
        telegram: Any,
        max_parallel: int = 2,
        timeout_seconds: int = 3600,
    ) -> None:
        self._db_factory = db_factory
        self._tuner_factory = param_tuner_factory
        self._telegram = telegram
        self._max_parallel = max_parallel
        self._timeout_seconds = timeout_seconds

    async def run(self) -> None:
        t0 = time.perf_counter()
        log.info("nightly_retrain_start")
        results: list[tuple[UUID, str]] = []

        async with self._db_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id FROM bots"
                        " WHERE deleted_at IS NULL AND is_shadow = false"
                        " AND status = 'running'"
                    )
                )
            ).all()
        bot_ids = [r[0] for r in rows]

        sem = asyncio.Semaphore(self._max_parallel)

        async def _retrain_one(bot_id: UUID) -> None:
            async with sem:
                try:
                    async with self._db_factory() as bot_db:
                        tuner = self._tuner_factory(bot_db)
                        await asyncio.wait_for(
                            tuner.trigger(bot_id, bot_db),
                            timeout=self._timeout_seconds,
                        )
                    results.append((bot_id, "triggered"))
                    m.orchestrator_retrain_bots_total.inc()
                except TimeoutError:
                    log.warning("retrain_timeout", bot_id=str(bot_id))
                    results.append((bot_id, "timeout"))
                except BaseException:
                    log.exception("retrain_failed", bot_id=str(bot_id))
                    results.append((bot_id, "error"))

        async with asyncio.TaskGroup() as tg:
            for bid in bot_ids:
                tg.create_task(_retrain_one(bid))

        elapsed = time.perf_counter() - t0
        m.orchestrator_retrain_latency_seconds.observe(elapsed)

        n_ok = sum(1 for _, s in results if s == "triggered")
        n_fail = len(results) - n_ok
        report = (
            f"Nightly retrain complete: {n_ok}/{len(results)} bots triggered"
            f" ({n_fail} errors). Elapsed: {elapsed:.1f}s"
        )
        if self._telegram is not None:
            await self._telegram.send(report)
        log.info("nightly_retrain_complete", n_bots=len(results), n_ok=n_ok, elapsed_s=elapsed)
