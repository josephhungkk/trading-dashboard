"""5c D2: periodic pending_fills sweeper.

Drains rows whose broker_order_id has since resolved to an orders.id but
weren't drained by the consumer's per-event drain (e.g. matching order was
written by a different path such as reconcile_at_startup). Also exports a
Prometheus gauge of rows older than 5 minutes for the
BrokerPendingFillsBacklog alert.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics

log = structlog.get_logger(__name__)


class PendingFillsSweeper:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
                log.exception("pending_fills_sweeper_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop_event.set()

    async def _tick(self) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "WITH resolvable AS ("
                    "  SELECT pf.exec_id, o.id AS order_id, pf.qty, pf.price, pf.currency, "
                    "         pf.executed_at, pf.commission, pf.commission_currency "
                    "    FROM pending_fills pf "
                    "    JOIN orders o ON o.broker_order_id = pf.broker_order_id "
                    "                  AND o.account_id = pf.account_id "
                    "), drained AS ("
                    "  DELETE FROM pending_fills "
                    "    WHERE exec_id IN (SELECT exec_id FROM resolvable) "
                    "  RETURNING exec_id"
                    ") "
                    "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at, "
                    "                   commission, commission_currency) "
                    "SELECT order_id, exec_id, qty, price, currency, executed_at, "
                    "       commission, commission_currency FROM resolvable "
                    "ON CONFLICT (exec_id) DO NOTHING"
                )
            )

            backlog = (
                await session.execute(
                    text("SELECT count(*) FROM pending_fills WHERE inserted_at < :cutoff"),
                    {"cutoff": datetime.now(UTC) - timedelta(minutes=5)},
                )
            ).scalar_one()
            metrics.pending_fills_backlog_count.set(int(backlog))
