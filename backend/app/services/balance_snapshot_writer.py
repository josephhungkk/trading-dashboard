"""Phase 10b.2 §4.3 — account_balance_snapshots writer with fail-OPEN.

Two-level nested SAVEPOINT pattern (architect HIGH #1): inner SAVEPOINT
isolates the snapshot INSERT so its failure doesn't roll back the
outer (NLV UPDATE) SAVEPOINT.

Tracked publish task set (architect HIGH #5): every redis.publish runs
as a tracked asyncio.Task; lifecycle managed by the BrokerDiscoverer
owning this writer instance, drained on stop().

Publish channel: portfolio.rollup.dirty. Subscribers (ws_portfolio.py)
debounce 500ms before recomputing + republishing snapshots.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

log = structlog.get_logger(__name__)

_INSERT_SNAPSHOT_SQL = text(
    """
    INSERT INTO account_balance_snapshots
      (account_id, ts, nlv, currency, source_label)
    VALUES (
      :account_id,
      clock_timestamp(),
      CAST(:nlv AS NUMERIC(20, 8)),
      :currency,
      :source_label
    )
    ON CONFLICT (account_id, ts) DO NOTHING
    """
)
# Review HIGH #6: clock_timestamp() (NOT now()) so per-row INSERTs in the same
# outer transaction get distinct timestamps. now() returns the transaction
# start time and is stable across the whole TX, which would collapse multi-
# account snapshots in a single _discover_nlv tick into the same ts and
# trigger silent ON CONFLICT drops.

_DIRTY_CHANNEL = "portfolio.rollup.dirty"


class BalanceSnapshotWriter:
    """Append-only snapshot writer with publish fan-out.

    Instantiated once per process in app.main lifespan; injected into
    BrokerDiscoverer. Owns the in-flight publish task set.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._publish_tasks: set[asyncio.Task[None]] = set()

    async def record(
        self,
        session: AsyncSession,
        *,
        account_id: UUID,
        nlv: str,
        currency: str,
        source_label: str,
    ) -> None:
        """Insert a snapshot row in an inner SAVEPOINT (fail-OPEN).

        MUST be called inside an outer ``session.begin_nested()`` (the
        NLV UPDATE SAVEPOINT). The inner SAVEPOINT here isolates the
        INSERT so a CheckViolation or similar does not roll back the
        outer SAVEPOINT.
        """
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT_SNAPSHOT_SQL,
                    {
                        "account_id": account_id,
                        "nlv": nlv,
                        "currency": currency,
                        "source_label": source_label,
                    },
                )
            metrics.portfolio_rollup_snapshot_writes_total.inc()
            # Phase 15b: expose NLV for crypto concentration check (15s TTL)
            if self._redis is not None:
                try:
                    await self._redis.set(
                        f"account:nlv:{account_id}:{currency}",
                        str(nlv),
                        ex=15,
                    )
                except Exception:
                    log.warning("crypto_nlv_redis_write_failed", account_id=str(account_id))
        except Exception:
            metrics.portfolio_rollup_snapshot_write_errors_total.inc()
            log.exception(
                "portfolio_rollup_snapshot_write_failed",
                account_id=str(account_id),
                source_label=source_label,
            )

    def schedule_publish(self, account_id: UUID) -> None:
        """Fire-and-forget Redis publish on portfolio.rollup.dirty.

        Tracked task set prevents GC-strand. Called AFTER the outer
        SAVEPOINT commits (so subscribers don't see a phantom dirty
        signal for a rolled-back snapshot).
        """
        if self._redis is None:
            return
        task = asyncio.create_task(self._publish(account_id))
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def _publish(self, account_id: UUID) -> None:
        try:
            await self._redis.publish(_DIRTY_CHANNEL, str(account_id))
            metrics.portfolio_rollup_ws_publish_total.inc()
        except Exception:
            metrics.portfolio_rollup_publish_failures_total.inc()
            log.warning(
                "portfolio_rollup_publish_failed",
                account_id=str(account_id),
            )

    async def stop(self) -> None:
        """Cancel and gather any in-flight publish tasks.

        Called from the BrokerDiscoverer's stop path or app lifespan
        shutdown. Idempotent — safe to call multiple times.
        """
        for t in list(self._publish_tasks):
            t.cancel()
        if self._publish_tasks:
            await asyncio.gather(*self._publish_tasks, return_exceptions=True)
        self._publish_tasks.clear()
