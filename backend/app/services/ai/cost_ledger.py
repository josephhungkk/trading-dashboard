"""Fire-and-forget AI completion cost ledger."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.core import metrics

log = structlog.get_logger(__name__)

_QUEUE_MAX = 1000
_BATCH_SIZE = 5
_FLUSH_INTERVAL_S = 2.0
_SHUTDOWN_DEADLINE_S = 5.0


@dataclass(frozen=True)
class CompletionRecord:
    request_id: str
    ts: datetime
    provider: str
    model: str
    capability: str
    prompt_tokens: int
    completion_tokens: int
    wall_time_ms: float
    outcome: str
    host: str | None = None


class CostLedger:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[CompletionRecord] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping.set()
        try:
            await asyncio.wait_for(
                self._drain_remaining(),
                timeout=_SHUTDOWN_DEADLINE_S,
            )
        except TimeoutError as exc:
            log.warning("cost_ledger_shutdown_drain_timeout", exc_info=exc)

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError as exc:
                log.debug("cost_ledger_task_cancelled", exc_info=exc)

    def record(self, rec: CompletionRecord) -> None:
        if self._stopping.is_set():
            return

        try:
            self._queue.put_nowait(rec)
        except asyncio.QueueFull as exc:
            log.debug("cost_ledger_queue_full", exc_info=exc)
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty as empty_exc:
                log.debug("cost_ledger_queue_empty_on_drop", exc_info=empty_exc)

            metrics.AI_COST_LEDGER_DROPS_TOTAL.inc()
            self._queue.put_nowait(rec)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)

    async def _collect_batch(self) -> list[CompletionRecord]:
        try:
            first = await asyncio.wait_for(
                self._queue.get(),
                timeout=_FLUSH_INTERVAL_S,
            )
        except TimeoutError as exc:
            log.debug("cost_ledger_collect_timeout", exc_info=exc)
            return []

        batch = [first]
        for _ in range(_BATCH_SIZE - 1):
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty as exc:
                log.debug("cost_ledger_collect_queue_empty", exc_info=exc)
                break
        return batch

    async def _drain_remaining(self) -> None:
        while not self._queue.empty():
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)

    async def _flush(self, batch: list[CompletionRecord]) -> None:
        stmt = text(
            "INSERT INTO ai_completions "
            "(request_id, ts, provider, model, capability, prompt_tokens, "
            "completion_tokens, wall_time_ms, outcome, host) "
            "VALUES (:request_id, :ts, :provider, :model, :capability, "
            ":prompt_tokens, :completion_tokens, :wall_time_ms, :outcome, :host)"
        )
        params_list = [
            {
                "request_id": rec.request_id,
                "ts": rec.ts,
                "provider": rec.provider,
                "model": rec.model,
                "capability": rec.capability,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "wall_time_ms": rec.wall_time_ms,
                "outcome": rec.outcome,
                "host": rec.host,
            }
            for rec in batch
        ]

        try:
            async with self._session_factory() as session:
                await session.execute(stmt, params_list)
                await session.commit()
        except Exception as exc:
            log.error(
                "cost_ledger_flush_failed",
                batch_size=len(batch),
                exc_info=exc,
            )
            metrics.AI_COST_LEDGER_INSERT_FAILURES_TOTAL.inc(len(batch))
