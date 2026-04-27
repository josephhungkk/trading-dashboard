"""Watchdog that recovers orders stuck in ``pending_submit``.

Background loop (every 30 s by default) scans for orders that have been in
``pending_submit`` for more than 60 seconds and attempts to reconcile them
against the broker's live order list:

* **Match found**: synthesise an ``OrderEventMessage`` and feed it through the
  ``OrderEventConsumer._process_event`` path so the normal state-machine
  transitions happen.  Emits ``broker_order_pending_submit_recovered_total``.

* **No match, age > 5 min**: escalate to ``rejected``, write an audit row in
  ``order_events``, and emit ``broker_order_pending_submit_orphan_total``.

``reconcile_at_startup()`` runs the same single-pass scan before consumer
streams open, closing the gap when the backend bounces mid-order (R9).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import Order, OrderEventMessage
from app.core import metrics

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# How long a pending_submit row must be stale before we check the broker.
_STALE_AFTER: timedelta = timedelta(seconds=60)
# How long before we give up and mark the order rejected.
_ORPHAN_AFTER: timedelta = timedelta(minutes=5)
# Recovery reason written into raw_payload.
_RECOVERY_OUTCOME_ORPHAN = "broker_no_match_after_5min"


class _BrokerClient(Protocol):
    label: str

    async def get_orders(self, account_number: str) -> list[Order]: ...


class _BrokerRegistry(Protocol):
    async def get_client(self, label: str) -> _BrokerClient: ...


class _EventConsumer(Protocol):
    async def _process_event(self, event: OrderEventMessage) -> None: ...


class PendingSubmitWatchdog:
    """Periodically reconciles ``pending_submit`` orders against the broker.

    Parameters
    ----------
    registry:
        Broker registry that can look up a sidecar client by gateway label.
    session_factory:
        SQLAlchemy async session factory (same one used by the rest of the app).
    consumer:
        ``OrderEventConsumer`` instance; its ``_process_event`` is called to
        synthesise recovery events so the normal state-machine path handles all
        DB writes and Redis fan-out.
    scan_interval:
        Seconds between watchdog ticks (default 30).
    """

    def __init__(
        self,
        registry: _BrokerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        consumer: _EventConsumer,
        *,
        scan_interval: float = 30.0,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._consumer = consumer
        self._scan_interval = scan_interval
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the background loop task."""
        self._task = asyncio.create_task(self._loop(), name="pending-submit-watchdog")

    async def stop(self) -> None:
        """Signal the loop to stop and await its completion."""
        self._stop_event.set()
        if self._task is not None:
            await self._task

    # ------------------------------------------------------------------
    # Public one-shot reconciliation (R9)
    # ------------------------------------------------------------------

    async def reconcile_at_startup(self) -> None:
        """One-shot scan run before consumer streams open.

        Eliminates the 'backend bounced mid-order' gap (R9): orders that
        transitioned at the broker while the backend was down will be
        recovered on the next startup before any streaming events arrive.
        """
        await self._scan_once()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            await self._scan_once()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self._scan_interval,
                )
                break  # stop_event fired during sleep
            except TimeoutError:
                pass  # normal tick

    # ------------------------------------------------------------------
    # Core scan
    # ------------------------------------------------------------------

    async def _scan_once(self) -> None:
        """Query all stale pending_submit rows and attempt reconciliation."""
        now = self._utcnow()
        stale_threshold = now - _STALE_AFTER
        orphan_threshold = now - _ORPHAN_AFTER

        async with self._session_factory() as session:
            rows = await self._fetch_stale_orders(session, stale_threshold)

        if not rows:
            return

        log.info(
            "pending_submit_watchdog_scan",
            stale_count=len(rows),
            stale_threshold=stale_threshold.isoformat(),
        )

        for row in rows:
            await self._reconcile_row(row, orphan_threshold=orphan_threshold, now=now)

    async def _fetch_stale_orders(
        self,
        session: AsyncSession,
        stale_threshold: datetime,
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                """
                SELECT
                    o.id,
                    o.client_order_id,
                    o.created_at,
                    ba.gateway_label,
                    ba.account_number,
                    ba.id AS account_id
                FROM orders o
                JOIN broker_accounts ba ON ba.id = o.account_id
                WHERE o.status = 'pending_submit'::order_status_enum
                  AND o.created_at < :stale_threshold
                ORDER BY o.created_at
                """
            ),
            {"stale_threshold": stale_threshold},
        )
        return [dict(r._mapping) for r in result]

    async def _reconcile_row(
        self,
        row: dict[str, Any],
        *,
        orphan_threshold: datetime,
        now: datetime,
    ) -> None:
        order_id: UUID = row["id"]
        client_order_id: UUID = row["client_order_id"]
        gateway_label: str = row["gateway_label"]
        account_number: str = row["account_number"]
        account_id: UUID = row["account_id"]
        created_at: datetime = (
            row["created_at"].replace(tzinfo=UTC)
            if row["created_at"].tzinfo is None
            else row["created_at"]
        )

        try:
            client = await self._registry.get_client(gateway_label)
            broker_orders = await client.get_orders(account_number)
        except Exception as exc:
            log.warning(
                "pending_submit_watchdog_broker_error",
                order_id=str(order_id),
                label=gateway_label,
                exc_info=exc,
            )
            return

        # Try to match by client_order_id (stored as orderRef on the broker side)
        matched: Order | None = None
        for broker_order in broker_orders:
            if broker_order.order_id == str(client_order_id):
                matched = broker_order
                break

        if matched is not None:
            await self._recover_via_synthetic_event(
                matched=matched,
                account_id=account_id,
                account_number=account_number,
                gateway_label=gateway_label,
            )
            metrics.broker_order_pending_submit_recovered_total.labels(label=gateway_label).inc()
            log.info(
                "pending_submit_watchdog_recovered",
                order_id=str(order_id),
                label=gateway_label,
                broker_order_id=matched.order_id,
            )
            return

        # No broker match — escalate to rejected if older than orphan threshold
        if created_at < orphan_threshold:
            await self._escalate_to_rejected(
                order_id=order_id,
                account_id=account_id,
                gateway_label=gateway_label,
                now=now,
            )
            metrics.broker_order_pending_submit_orphan_total.labels(label=gateway_label).inc()
            log.warning(
                "pending_submit_watchdog_orphan",
                order_id=str(order_id),
                label=gateway_label,
            )

    async def _recover_via_synthetic_event(
        self,
        *,
        matched: Order,
        account_id: UUID,
        account_number: str,
        gateway_label: str,
    ) -> None:
        raw_payload = json.dumps(
            {
                "account_id": str(account_id),
                "gateway_label": gateway_label,
                "account_number": account_number,
                "recovery_source": "pending_submit_watchdog",
            }
        )
        msg = OrderEventMessage(
            broker_order_id=matched.order_id,
            client_order_id=matched.order_id,  # order_id == client_order_id in broker
            status=matched.status,
            filled_qty=matched.quantity_filled,
            avg_fill_price=matched.avg_fill_price.value if matched.avg_fill_price else "",
            broker_event_at=matched.updated_at or self._utcnow(),
            raw_payload=raw_payload,
        )
        await self._consumer._process_event(msg)

    async def _escalate_to_rejected(
        self,
        *,
        order_id: UUID,
        account_id: UUID,
        gateway_label: str,
        now: datetime,
    ) -> None:
        """Transition order to rejected and insert an audit order_events row atomically."""
        recovery_payload: dict[str, str] = {
            "recovery_outcome": _RECOVERY_OUTCOME_ORPHAN,
            "recovery_source": "pending_submit_watchdog",
        }
        raw_payload_json = json.dumps(recovery_payload)

        async with self._session_factory() as session, session.begin():
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        UPDATE orders
                           SET status = 'rejected'::order_status_enum,
                               updated_at = :now,
                               last_event_at = :now
                         WHERE id = :order_id
                           AND status = 'pending_submit'::order_status_enum
                        """
                    ),
                    {"order_id": order_id, "now": now},
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO order_events (
                            order_id,
                            account_id,
                            broker_order_id,
                            status,
                            filled_qty,
                            avg_fill_price,
                            broker_event_at,
                            observed_at,
                            raw_payload
                        )
                        VALUES (
                            :order_id,
                            :account_id,
                            NULL,
                            'rejected'::order_status_enum,
                            NULL,
                            NULL,
                            :now,
                            :now,
                            CAST(:raw_payload AS jsonb)
                        )
                        """
                    ),
                    {
                        "order_id": order_id,
                        "account_id": account_id,
                        "now": now,
                        "raw_payload": raw_payload_json,
                    },
                )

    def _utcnow(self) -> datetime:
        """Return current UTC time; override in tests via monkeypatch."""
        return datetime.now(UTC)
