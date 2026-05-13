"""Phase 11a-C HIGH-8: orphan-recovery sweeper for ai_jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import text

from app.core import metrics

log = structlog.get_logger(__name__)

_SWEEP_INTERVAL_S = 30.0
_WARMING_CUTOFF_S = 90  # spec: WoL + readiness should always complete within
_INFERRING_CUTOFF_S = 600  # 10 min - 70B prompts need room

_WARMING_UPDATE_SQL = text(
    """
    UPDATE ai_jobs
    SET status = 'failed', error = 'be_restart', completed_at = NOW()
    WHERE status = 'warming'
      AND warming_started_at < NOW() - make_interval(secs => :cutoff)
    RETURNING id
    """
)

_INFERRING_UPDATE_SQL = text(
    """
    UPDATE ai_jobs
    SET status = 'failed', error = 'be_restart', completed_at = NOW()
    WHERE status = 'inferring'
      AND inferring_started_at < NOW() - make_interval(secs => :cutoff)
    RETURNING id
    """
)


async def sweep_orphans_once(session_factory: Callable[[], Any]) -> int:
    """Single sweep iteration. Returns number of rows transitioned."""
    async with session_factory() as session:
        warming_result = await session.execute(
            _WARMING_UPDATE_SQL,
            {"cutoff": _WARMING_CUTOFF_S},
        )
        warming_ids = list(warming_result.scalars().all())

        inferring_result = await session.execute(
            _INFERRING_UPDATE_SQL,
            {"cutoff": _INFERRING_CUTOFF_S},
        )
        inferring_ids = list(inferring_result.scalars().all())
        await session.commit()

    for job_id in warming_ids:
        metrics.ai_jobs_orphan_recovered_total.labels(phase="warming").inc()
        log.info("ai_jobs_orphan_recovered", job_id=str(job_id), phase="warming")
    for job_id in inferring_ids:
        metrics.ai_jobs_orphan_recovered_total.labels(phase="inferring").inc()
        log.info("ai_jobs_orphan_recovered", job_id=str(job_id), phase="inferring")

    return len(warming_ids) + len(inferring_ids)


async def run_orphan_sweeper(session_factory: Callable[[], Any]) -> None:
    """Background task - sweep every 30s until cancelled."""
    while True:
        try:
            await sweep_orphans_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:  # Sweeper must never die on transient errors.
            log.exception("ai_jobs_orphan_sweep_failed")
        await asyncio.sleep(_SWEEP_INTERVAL_S)
