"""Entry point for the backtest_worker Docker service."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.redis_client import get_redis_pool

logger = structlog.get_logger(__name__)

_WORKER_ID = str(uuid.uuid4())
_QUEUE_KEY = "backtest:queue"
_PENDING_KEY = f"backtest:pending:{_WORKER_ID}"
_CONCURRENCY = int(os.getenv("BACKTEST_WORKER_CONCURRENCY", "2"))
_ORPHAN_STALE_MINUTES = 120  # backtest runs can exceed 5 min on long date ranges
_ORPHAN_INTERVAL = 60


async def orphan_sweep(redis) -> None:
    while True:
        await asyncio.sleep(_ORPHAN_INTERVAL)
        cutoff = datetime.now(UTC) - timedelta(minutes=_ORPHAN_STALE_MINUTES)
        try:
            async with SessionLocal() as sweep_db:
                result = await sweep_db.execute(
                    text("""
                        UPDATE backtests SET status='queued', started_at=NULL
                        WHERE status='running' AND started_at < :cutoff
                        RETURNING id
                    """),
                    {"cutoff": cutoff},
                )
                rows = result.fetchall()
                await sweep_db.commit()
            for (bid,) in rows:
                await redis.rpush(_QUEUE_KEY, str(bid))
                logger.info("backtest_orphan_requeued", backtest_id=str(bid))
        except Exception:
            logger.exception("backtest_orphan_sweep_error")


async def main() -> None:
    from app.backtest.runner import BacktestRunner

    redis = await get_redis_pool()
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    _background_tasks: set[asyncio.Task] = set()

    sweep = asyncio.create_task(orphan_sweep(redis))
    _background_tasks.add(sweep)
    sweep.add_done_callback(_background_tasks.discard)

    while True:
        job_id = await redis.blmove(_QUEUE_KEY, _PENDING_KEY, "LEFT", "RIGHT", timeout=0)
        if job_id is None:
            continue
        if isinstance(job_id, bytes):
            job_id = job_id.decode()

        async with SessionLocal() as job_db:
            runner = BacktestRunner(db=job_db, redis=redis, semaphore=semaphore)
            task = asyncio.create_task(_run_and_cleanup(runner, job_id, redis))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)


async def _run_and_cleanup(runner, job_id: str, redis) -> None:
    try:
        await runner._replay(job_id)
    finally:
        await redis.lrem(f"backtest:pending:{_WORKER_ID}", 1, job_id)


if __name__ == "__main__":
    asyncio.run(main())
