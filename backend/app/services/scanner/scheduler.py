from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from app.core import metrics

log = structlog.get_logger()

PRESET_CRONS: dict[str, str] = {
    "every_5m": "*/5 * * * *",
    "every_15m": "*/15 * * * *",
    "hourly": "0 * * * *",
    "market_open": "30 9 * * 1-5",
}


class ScannerScheduler:
    def __init__(self, *, scheduler: AsyncIOScheduler, scanner_service: Any) -> None:
        self._scheduler = scheduler
        self._svc = scanner_service
        self._locks: dict[str, asyncio.Lock] = {}

    def validate_cron(self, expr: str) -> bool:
        try:
            croniter(expr)
            return True
        except ValueError:
            return False

    async def rebuild_from_db(self, db: Any) -> None:
        import sqlalchemy as sa

        rows = await db.execute(
            sa.text(
                "SELECT id, schedule, market_hours_gate, exchange "
                "FROM saved_scans WHERE enabled = true AND schedule IS NOT NULL"
            )
        )
        for row in rows.fetchall():
            await self.schedule_scan(
                scan_id=row.id,
                cron_expr=row.schedule,
                market_hours_gate=row.market_hours_gate,
                exchange=row.exchange,
            )

    async def schedule_scan(
        self,
        *,
        scan_id: UUID,
        cron_expr: str,
        market_hours_gate: bool,
        exchange: str | None,
    ) -> None:
        lock = self._locks.setdefault(str(scan_id), asyncio.Lock())
        async with lock:
            try:
                self._scheduler.remove_job(str(scan_id))
            except JobLookupError:
                pass
            self._scheduler.add_job(
                self._fire,
                CronTrigger.from_crontab(cron_expr),
                id=str(scan_id),
                args=[scan_id, market_hours_gate, exchange],
                coalesce=True,
                misfire_grace_time=60,
            )
            log.info("scanner.scheduler.scheduled", scan_id=str(scan_id), cron=cron_expr)
            metrics.scanner_scheduler_jobs.set(len(self._scheduler.get_jobs()))

    async def remove_scan(self, scan_id: UUID) -> None:
        lock = self._locks.setdefault(str(scan_id), asyncio.Lock())
        async with lock:
            try:
                self._scheduler.remove_job(str(scan_id))
            except JobLookupError:
                pass
        self._locks.pop(str(scan_id), None)
        metrics.scanner_scheduler_jobs.set(len(self._scheduler.get_jobs()))

    async def _fire(self, scan_id: UUID, market_hours_gate: bool, exchange: str | None) -> None:
        if market_hours_gate and exchange:
            from app.services.market_calendar import is_open

            if not is_open(exchange):
                log.info(
                    "scanner.scheduler.skipped_market_closed",
                    scan_id=str(scan_id),
                    exchange=exchange,
                )
                return
        try:
            await self._svc.run_scan(scan_id=scan_id)
            metrics.scanner_scheduler_fires_total.labels(scan_id=str(scan_id), status="ok").inc()
        except Exception:
            log.exception("scanner.scheduler.fire_error", scan_id=str(scan_id))
            metrics.scanner_scheduler_fires_total.labels(scan_id=str(scan_id), status="error").inc()
