"""Broker order event consumer.

One child task tails each ``(gateway_label, account_number)`` broker stream,
records every event in ``order_events``, updates the materialized ``orders``
row when the event belongs to a backend-placed order, and publishes the event
to Redis for SSE fan-out.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import Order, OrderEventMessage
from app.core import metrics
from app.services.brokers import BrokerRegistry

log = structlog.get_logger(__name__)

TERMINAL_STATUSES = ("filled", "cancelled", "rejected", "expired")
MAX_CONSECUTIVE_FAILURES = 50


# 5c MED-5: in-memory buffer for commissionReport events that arrive before
# the matching fill row has been written. 5-min TTL.
_COMMISSION_BUFFER: dict[str, tuple[float, str, str]] = {}
_COMMISSION_BUFFER_TTL_SECONDS: float = 300.0


def _commission_buffer_set(exec_id: str, commission: str, currency: str) -> None:
    _COMMISSION_BUFFER[exec_id] = (
        time.monotonic() + _COMMISSION_BUFFER_TTL_SECONDS,
        commission,
        currency,
    )
    if len(_COMMISSION_BUFFER) > 1000:
        metrics.commission_buffer_overflow_total.inc()


def _commission_buffer_pop(exec_id: str) -> tuple[str, str] | None:
    entry = _COMMISSION_BUFFER.pop(exec_id, None)
    if entry is None:
        return None
    expires, commission, currency = entry
    if time.monotonic() > expires:
        return None
    return commission, currency


@dataclass(frozen=True)
class AccountStream:
    account_id: UUID
    label: str
    account_number: str


@dataclass(frozen=True)
class AccountChangedEvent:
    kind: str
    label: str
    account_number: str
    account_id: UUID | None = None


class _BrokerClient(Protocol):
    label: str

    def order_event_stream(self, account_number: str) -> AsyncIterator[OrderEventMessage]: ...

    async def get_orders(self, account_number: str) -> list[Order]: ...


class _AccountEventQueue(Protocol):
    async def get(self) -> object: ...


class _RedisPublisher(Protocol):
    async def publish(self, channel: str, message: str | bytes) -> object: ...


_stream_context: contextvars.ContextVar[AccountStream | None] = contextvars.ContextVar(
    "order_event_consumer_stream_context",
    default=None,
)


class OrderEventConsumer:
    def __init__(
        self,
        registry: BrokerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._redis = cast(_RedisPublisher, redis)
        self._stop_event = asyncio.Event()
        self._supervisor_task: asyncio.Task[None] | None = None
        self._children: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._account_ids: dict[tuple[str, str], UUID] = {}
        self._supervisor_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._failure_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._supervisor_task is not None and not self._supervisor_task.done():
            return
        self._stop_event.clear()
        self._supervisor_task = asyncio.create_task(
            self._supervisor(),
            name="broker-order-event-supervisor",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._children.values())
        if self._supervisor_task is not None:
            tasks.append(self._supervisor_task)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=30.0)
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
        self._children.clear()
        self._account_ids.clear()
        self._supervisor_task = None

    async def _supervisor(self) -> None:
        account_events = self._account_changed_events()
        while not self._stop_event.is_set():
            await self._supervisor_iteration()
            try:
                if account_events is None:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=60.0)
                else:
                    event = await asyncio.wait_for(account_events.__anext__(), timeout=60.0)
                    await self._handle_account_changed(event)
            except StopAsyncIteration:
                account_events = None
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(
                    "broker_order_event_supervisor_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    async def _supervisor_iteration(self) -> None:
        if self._supervisor_lock.locked():
            log.warning("broker_order_event_supervisor_iteration_skipped_overlap")
            return
        async with self._supervisor_lock:
            rows = await self._enumerate_accounts()
            active_keys = {(row.label, row.account_number) for row in rows}
            for row in rows:
                self._spawn_child(row)
            for key in set(self._children) - active_keys:
                await self._cancel_child(key)

    async def _run_account_stream(self, label: str, account_number: str) -> None:
        key = (label, account_number)
        backoff_seconds = 1.0
        account = await self._resolve_account(label, account_number)
        token = _stream_context.set(account)
        metrics.consumer_alive.labels(label=label, account_id=str(account.account_id)).set(1)
        try:
            while not self._stop_event.is_set():
                try:
                    client = cast(_BrokerClient, await self._registry.get_client(label))
                    await self._run_account_stream_once(client, label, account_number, account)
                    backoff_seconds = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    metrics.broker_order_stream_reconnects_total.labels(label=label).inc()
                    log.warning(
                        "broker_order_event_stream_error",
                        label=label,
                        account_number=account_number,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        reconnect_in_seconds=backoff_seconds,
                    )
                    await self._sleep_or_stop(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 30.0)
        finally:
            metrics.consumer_alive.labels(label=label, account_id=str(account.account_id)).set(0)
            _stream_context.reset(token)
            self._children.pop(key, None)

    async def _run_account_stream_once(
        self,
        client: _BrokerClient,
        label: str,
        account_number: str,
        account: AccountStream,
    ) -> None:
        """Buffer-then-drain: open gRPC stream, snapshot, resync, then tail.

        R11 ordering guarantee:
        1. Open the live gRPC stream and start buffering events immediately.
        2. Call get_orders() for a snapshot and emit synthetic events for any
           order whose status is new or changed relative to local DB.
        3. Drain the buffered live events (UPSERT predicate prevents older
           synthetic events from overwriting newer live ones).
        4. Continue tailing the live stream inline.
        """
        buffer: asyncio.Queue[OrderEventMessage | None] = asyncio.Queue()
        # 5c v0.5.5 diagnostic: log when the stream actually subscribes/closes.
        log.info("stream_subscribed", label=label, account_number=account_number)

        async def _fill_buffer() -> None:
            try:
                async for event in client.order_event_stream(account_number):
                    await buffer.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "stream_buffer_fill_error",
                    label=label,
                    account_number=account_number,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                log.info("stream_closed", label=label, account_number=account_number)
                await buffer.put(None)  # sentinel

        filler = asyncio.create_task(_fill_buffer(), name=f"order-event-buffer-{label}")
        try:
            # Phase 1 — resync from snapshot (before processing any live events)
            snapshot_orders = await client.get_orders(account_number)
            synth_events = await self._synthesize_resync_events(
                account, label, account_number, snapshot_orders
            )
            for synth in synth_events:
                metrics.broker_order_stream_resync_synthetic_events_total.labels(label=label).inc()
                await self._process_event(synth)

            # Phase 2 — drain buffered events that arrived during snapshot
            while not buffer.empty():
                event = buffer.get_nowait()
                if event is None:
                    return
                metrics.broker_order_events_received_total.labels(label=label).inc()
                self._observe_lag(label, event.broker_event_at)
                await self._process_event(event)

            # Phase 3 — tail live stream
            while not self._stop_event.is_set():
                event = await buffer.get()
                if event is None:
                    return
                metrics.broker_order_events_received_total.labels(label=label).inc()
                self._observe_lag(label, event.broker_event_at)
                await self._process_event(event)
        finally:
            filler.cancel()
            try:
                await filler
            except asyncio.CancelledError:
                pass

    async def _synthesize_resync_events(
        self,
        account: AccountStream,
        label: str,
        account_number: str,
        snapshot_orders: list[Order],
    ) -> list[OrderEventMessage]:
        """Build synthetic OrderEventMessages for snapshot orders that differ from DB state.

        For each order in the snapshot whose client_order_id is absent from the
        local ``orders`` table OR whose status differs from the DB row, we emit a
        synthetic event. Synthetic events are sorted by broker_event_at so they
        are processed in chronological order. The UPSERT predicate on
        ``last_event_at`` in _update_order ensures stale synthetic events never
        overwrite newer live events.
        """
        if not snapshot_orders:
            return []

        # Fetch known client_order_ids and their last known state from the DB.
        known: dict[str, tuple[str, datetime | None]] = {}
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT client_order_id::text, status::text, last_event_at
                      FROM orders
                     WHERE account_id = :account_id
                    """
                ),
                {"account_id": account.account_id},
            )
            for row in result.mappings().all():
                known[str(row["client_order_id"])] = (
                    str(row["status"]),
                    row["last_event_at"],
                )

        synth: list[OrderEventMessage] = []
        import json as _json

        for order in snapshot_orders:
            client_order_id = order.order_id  # order_id is the client_order_id handle
            snapshot_status = order.status.lower().removeprefix("status_")
            # Normalise proto enum -> DB status
            status_aliases: dict[str, str] = {
                "pending": "pending_submit",
                "submitted": "submitted",
                "partial": "partial",
                "filled": "filled",
                "cancelled": "cancelled",
                "rejected": "rejected",
                "expired": "expired",
                "inactive": "inactive",
                "modified": "modified",
                "unspecified": "pending_submit",
                "status_unspecified": "pending_submit",
            }
            normalised_status = status_aliases.get(snapshot_status, snapshot_status)

            db_row = known.get(client_order_id)
            if db_row is not None:
                db_status, _ = db_row
                if db_status == normalised_status:
                    continue  # no change — skip

            # Determine broker_event_at: prefer order.updated_at then submitted_at
            broker_event_at = order.updated_at or order.submitted_at

            raw = {
                "account_id": str(account.account_id),
                "gateway_label": label,
                "account_number": account_number,
                "synthetic": True,
            }
            synth.append(
                OrderEventMessage(
                    broker_order_id=order.order_id,
                    client_order_id=order.order_id,
                    status=normalised_status,
                    filled_qty=order.quantity_filled,
                    avg_fill_price=order.avg_fill_price.value,
                    broker_event_at=broker_event_at,
                    raw_payload=_json.dumps(raw),
                )
            )

        synth.sort(key=lambda e: e.broker_event_at or datetime.min.replace(tzinfo=UTC))
        return synth

    async def _process_event(self, event: OrderEventMessage) -> None:
        label = self._event_label(event)
        if event.kind == "commission_report" and event.exec_id:
            cr_payload = _parse_raw_payload(event.raw_payload)
            payload_dict = cr_payload if isinstance(cr_payload, dict) else {}
            commission = str(payload_dict.get("commission", "0"))
            commission_currency = str(payload_dict.get("commission_currency", "USD")).upper()
            async with self._session_factory() as session, session.begin():
                result = await session.execute(
                    text(
                        "UPDATE fills SET commission = :c, commission_currency = :cc "
                        "WHERE exec_id = :e"
                    ),
                    {"c": commission, "cc": commission_currency, "e": event.exec_id},
                )
                if (getattr(result, "rowcount", None) or 0) == 0:
                    _commission_buffer_set(event.exec_id, commission, commission_currency)
            async with self._failure_lock:
                self._consecutive_failures = 0
            return
        try:
            account = await self._account_for_event(event)
            broker_event_at = event.broker_event_at or datetime.now(UTC)
            raw_payload = _parse_raw_payload(event.raw_payload)
            status = _normalize_status(event.status)
            filled_qty = _parse_decimal(event.filled_qty, field="filled_qty")
            avg_fill_price = _parse_decimal(event.avg_fill_price, field="avg_fill_price")
            client_order_id = _parse_client_order_id(event.client_order_id)

            async with self._session_factory() as session, session.begin():
                async with session.begin_nested():
                    order_id = await self._matching_order_id(session, account, client_order_id)
                    event_id = await self._insert_order_event(
                        session,
                        account=account,
                        event=event,
                        order_id=order_id,
                        status=status,
                        filled_qty=filled_qty,
                        avg_fill_price=avg_fill_price,
                        broker_event_at=broker_event_at,
                        raw_payload=raw_payload,
                    )
                    if client_order_id is not None:
                        await self._update_order(
                            session,
                            account=account,
                            event=event,
                            client_order_id=client_order_id,
                            status=status,
                            filled_qty=filled_qty,
                            avg_fill_price=avg_fill_price,
                            broker_event_at=broker_event_at,
                        )
                    if event.exec_id and event.kind == "exec_details":
                        await self._record_fill(
                            session,
                            account=account,
                            event=event,
                            order_id=order_id,
                            broker_event_at=broker_event_at,
                            raw_payload=raw_payload,
                        )
                    if order_id is not None and event.broker_order_id:
                        await self._drain_pending_fills(
                            session,
                            order_id=order_id,
                            broker_order_id=event.broker_order_id,
                        )
                    if status == "cancelled" and order_id is not None:
                        parent_cancel_at = (
                            await session.execute(
                                text(
                                    "SELECT p.cancel_requested_at FROM orders o "
                                    "JOIN orders p ON p.id = o.parent_order_id "
                                    "WHERE o.id = :oid AND p.cancel_requested_at IS NOT NULL"
                                ),
                                {"oid": order_id},
                            )
                        ).scalar_one_or_none()
                        if parent_cancel_at is not None:
                            latency = (broker_event_at - parent_cancel_at).total_seconds()
                            metrics.broker_bracket_cancel_cascade_seconds.observe(max(latency, 0.0))
                    payload = await self._publish_payload(session, event_id)

            await self._publish(account.account_id, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._record_event_failure(label, event, exc)
            return

        async with self._failure_lock:
            self._consecutive_failures = 0

    async def _record_event_failure(
        self,
        label: str,
        event: OrderEventMessage,
        exc: Exception,
    ) -> None:
        async with self._failure_lock:
            self._consecutive_failures += 1
            failures = self._consecutive_failures
        metrics.broker_order_events_dropped_total.labels(
            label=label,
            reason=type(exc).__name__,
        ).inc()
        log.exception(
            "broker_order_event_process_failed",
            label=label,
            broker_order_id=event.broker_order_id,
            raw_payload=_parse_raw_payload_best_effort(event.raw_payload),
            consecutive_failures=failures,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        if failures >= MAX_CONSECUTIVE_FAILURES:
            log.error(
                "broker_order_event_circuit_breaker_tripped",
                label=label,
                consecutive_failures=failures,
            )
            raise RuntimeError("broker order event consumer circuit breaker tripped") from exc

    async def _enumerate_accounts(self) -> list[AccountStream]:
        stmt = text(
            """
            SELECT id, gateway_label, account_number
              FROM broker_accounts
             WHERE deleted_at IS NULL
             ORDER BY gateway_label, account_number
            """
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return [
                AccountStream(
                    account_id=UUID(str(row["id"])),
                    label=str(row["gateway_label"]),
                    account_number=str(row["account_number"]),
                )
                for row in result.mappings().all()
            ]

    async def _resolve_account(self, label: str, account_number: str) -> AccountStream:
        stmt = text(
            """
            SELECT id, gateway_label, account_number
              FROM broker_accounts
             WHERE gateway_label = :label
               AND account_number = :account_number
               AND deleted_at IS NULL
            """
        )
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        stmt,
                        {"label": label, "account_number": account_number},
                    )
                )
                .mappings()
                .one()
            )
        return AccountStream(
            account_id=UUID(str(row["id"])),
            label=str(row["gateway_label"]),
            account_number=str(row["account_number"]),
        )

    async def _account_for_event(self, event: OrderEventMessage) -> AccountStream:
        context = _stream_context.get()
        if context is not None:
            return context

        payload = _parse_raw_payload_best_effort(event.raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("order event missing account context")
        account_id = payload.get("account_id")
        label = payload.get("label") or payload.get("gateway_label")
        account_number = (
            payload.get("account_number") or payload.get("account") or payload.get("acctNumber")
        )
        if account_id is not None and label is not None and account_number is not None:
            return AccountStream(
                account_id=UUID(str(account_id)),
                label=str(label),
                account_number=str(account_number),
            )
        if label is not None and account_number is not None:
            return await self._resolve_account(str(label), str(account_number))
        raise ValueError("order event missing account context")

    async def _matching_order_id(
        self,
        session: AsyncSession,
        account: AccountStream,
        client_order_id: UUID | None,
    ) -> UUID | None:
        if client_order_id is None:
            return None
        result = await session.execute(
            text(
                """
                SELECT id
                  FROM orders
                 WHERE account_id = :account_id
                   AND client_order_id = :client_order_id
                """
            ),
            {"account_id": account.account_id, "client_order_id": client_order_id},
        )
        row = result.first()
        if row is None:
            return None
        return UUID(str(row[0]))

    async def _insert_order_event(
        self,
        session: AsyncSession,
        *,
        account: AccountStream,
        event: OrderEventMessage,
        order_id: UUID | None,
        status: str,
        filled_qty: Decimal | None,
        avg_fill_price: Decimal | None,
        broker_event_at: datetime,
        raw_payload: dict[str, Any] | list[Any] | None,
    ) -> int:
        result = await session.execute(
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
                    raw_payload
                )
                VALUES (
                    :order_id,
                    :account_id,
                    :broker_order_id,
                    CAST(:status AS order_status_enum),
                    :filled_qty,
                    :avg_fill_price,
                    :broker_event_at,
                    CAST(:raw_payload AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "order_id": order_id,
                "account_id": account.account_id,
                "broker_order_id": event.broker_order_id or None,
                "status": status,
                "filled_qty": filled_qty,
                "avg_fill_price": avg_fill_price,
                "broker_event_at": broker_event_at,
                "raw_payload": json.dumps(raw_payload) if raw_payload is not None else None,
            },
        )
        return int(result.scalar_one())

    async def _update_order(
        self,
        session: AsyncSession,
        *,
        account: AccountStream,
        event: OrderEventMessage,
        client_order_id: UUID,
        status: str,
        filled_qty: Decimal | None,
        avg_fill_price: Decimal | None,
        broker_event_at: datetime,
    ) -> None:
        await session.execute(
            text(
                """
                UPDATE orders
                   SET status = CASE
                         WHEN orders.status IN ('filled', 'cancelled', 'rejected', 'expired')
                           THEN orders.status
                         WHEN order_status_rank(orders.status)
                              > order_status_rank(CAST(:new_status AS order_status_enum))
                           THEN orders.status
                         ELSE CAST(:new_status AS order_status_enum)
                       END,
                       broker_order_id = COALESCE(orders.broker_order_id, :broker_order_id),
                       filled_qty = GREATEST(
                           orders.filled_qty,
                           COALESCE(CAST(:filled_qty AS NUMERIC), orders.filled_qty)
                       ),
                       avg_fill_price = CAST(:avg_fill_price AS NUMERIC),
                       notional_filled = COALESCE(CAST(:filled_qty AS NUMERIC), 0)
                                         * COALESCE(CAST(:avg_fill_price AS NUMERIC), 0),
                       last_event_at = GREATEST(
                           COALESCE(orders.last_event_at, '-infinity'::timestamptz),
                           :broker_event_at
                       ),
                       updated_at = now()
                 WHERE account_id = :account_id
                   AND client_order_id = :client_order_id
                   AND :broker_event_at >= COALESCE(
                       orders.last_event_at,
                       '-infinity'::timestamptz
                   )
                """
            ),
            {
                "new_status": status,
                "broker_order_id": event.broker_order_id or None,
                "filled_qty": filled_qty,
                "avg_fill_price": avg_fill_price,
                "broker_event_at": broker_event_at,
                "account_id": account.account_id,
                "client_order_id": client_order_id,
            },
        )

    async def _record_fill(
        self,
        session: AsyncSession,
        *,
        account: AccountStream,
        event: OrderEventMessage,
        order_id: UUID | None,
        broker_event_at: datetime,
        raw_payload: dict[str, Any] | list[Any] | None,
    ) -> None:
        payload_dict = raw_payload if isinstance(raw_payload, dict) else {}
        currency = str(payload_dict.get("currency") or "USD").upper()
        if order_id is None:
            await session.execute(
                text(
                    "INSERT INTO pending_fills (exec_id, broker_order_id, account_id, "
                    "qty, price, currency, executed_at, raw_payload) "
                    "VALUES (:e, :bo, :a, :q, :p, :c, :ts, CAST(:rp AS jsonb)) "
                    "ON CONFLICT (exec_id) DO NOTHING"
                ),
                {
                    "e": event.exec_id,
                    "bo": event.broker_order_id,
                    "a": account.account_id,
                    "q": event.filled_qty or "0",
                    "p": event.avg_fill_price or "0",
                    "c": currency,
                    "ts": broker_event_at,
                    "rp": event.raw_payload or "{}",
                },
            )
            return
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at) "
                        "VALUES (:o, :e, :q, :p, :c, :ts) "
                        "ON CONFLICT (exec_id) DO NOTHING"
                    ),
                    {
                        "o": order_id,
                        "e": event.exec_id,
                        "q": event.filled_qty or "0",
                        "p": event.avg_fill_price or "0",
                        "c": currency,
                        "ts": broker_event_at,
                    },
                )
                buffered = _commission_buffer_pop(event.exec_id)
                if buffered:
                    buf_commission, buf_currency = buffered
                    await session.execute(
                        text(
                            "UPDATE fills SET commission = :c, commission_currency = :cc "
                            "WHERE exec_id = :e"
                        ),
                        {"c": buf_commission, "cc": buf_currency, "e": event.exec_id},
                    )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23503":
                raise
            await session.execute(
                text(
                    "INSERT INTO pending_fills (exec_id, broker_order_id, account_id, "
                    "qty, price, currency, executed_at, raw_payload) "
                    "VALUES (:e, :bo, :a, :q, :p, :c, :ts, CAST(:rp AS jsonb)) "
                    "ON CONFLICT (exec_id) DO NOTHING"
                ),
                {
                    "e": event.exec_id,
                    "bo": event.broker_order_id,
                    "a": account.account_id,
                    "q": event.filled_qty or "0",
                    "p": event.avg_fill_price or "0",
                    "c": currency,
                    "ts": broker_event_at,
                    "rp": event.raw_payload or "{}",
                },
            )

    async def _drain_pending_fills(
        self,
        session: AsyncSession,
        *,
        order_id: UUID,
        broker_order_id: str,
    ) -> None:
        await session.execute(
            text(
                "WITH drained AS ("
                "  DELETE FROM pending_fills WHERE broker_order_id = :bo "
                "  RETURNING exec_id, qty, price, currency, executed_at, "
                "            commission, commission_currency"
                ") "
                "INSERT INTO fills (order_id, exec_id, qty, price, currency, executed_at, "
                "                   commission, commission_currency) "
                "SELECT :o, exec_id, qty, price, currency, executed_at, "
                "       commission, commission_currency FROM drained "
                "ON CONFLICT (exec_id) DO NOTHING"
            ),
            {"o": order_id, "bo": broker_order_id},
        )

    async def _publish_payload(self, session: AsyncSession, event_id: int) -> str:
        # Two distinct bind names so asyncpg's per-parameter type inference
        # doesn't collapse `$1` to a single ambiguous shape (text-vs-bigint).
        result = await session.execute(
            text(
                """
                SELECT jsonb_build_object(
                    'id', CAST(:event_id_text AS text),
                    'event_id', CAST(:event_id_int AS bigint),
                    'order_id', oe.order_id,
                    'account_id', oe.account_id,
                    'broker_order_id', oe.broker_order_id,
                    'status', oe.status,
                    'filled_qty', oe.filled_qty,
                    'avg_fill_price', oe.avg_fill_price,
                    'broker_event_at', oe.broker_event_at,
                    'observed_at', oe.observed_at,
                    'raw_payload', oe.raw_payload
                )::text
                  FROM order_events oe
                 WHERE oe.id = CAST(:event_id_int AS bigint)
                """
            ),
            {"event_id_text": str(event_id), "event_id_int": event_id},
        )
        return str(result.scalar_one())

    async def _publish(self, account_id: UUID, payload: str) -> None:
        await self._redis.publish("orders:events:fleet", payload)
        await self._redis.publish(f"orders:events:account:{account_id}", payload)

    def _spawn_child(self, account: AccountStream) -> None:
        key = (account.label, account.account_number)
        self._account_ids = {**self._account_ids, key: account.account_id}
        existing = self._children.get(key)
        if existing is not None and not existing.done():
            return
        self._children[key] = asyncio.create_task(
            self._run_account_stream(account.label, account.account_number),
            name=f"broker-order-event-{account.label}-{account.account_number}",
        )

    async def _cancel_child(self, key: tuple[str, str]) -> None:
        task = self._children.pop(key, None)
        self._account_ids.pop(key, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _handle_account_changed(self, event: object) -> None:
        changed = _coerce_account_changed_event(event)
        if changed is None:
            return
        if changed.kind == "add":
            account_id = changed.account_id
            if account_id is None:
                account = await self._resolve_account(changed.label, changed.account_number)
            else:
                account = AccountStream(
                    account_id=account_id,
                    label=changed.label,
                    account_number=changed.account_number,
                )
            self._spawn_child(account)
            return
        if changed.kind == "remove":
            await self._cancel_child((changed.label, changed.account_number))

    def _account_changed_events(self) -> AsyncIterator[object] | None:
        subscribe = getattr(self._registry, "subscribe_account_changed", None)
        if callable(subscribe):
            stream = subscribe()
            if hasattr(stream, "__anext__"):
                return cast(AsyncIterator[object], stream)

        account_changed = getattr(self._registry, "account_changed", None)
        if account_changed is None:
            return None
        if hasattr(account_changed, "__anext__"):
            return cast(AsyncIterator[object], account_changed)
        if hasattr(account_changed, "get"):
            queue = cast(_AccountEventQueue, account_changed)
            return _queue_iterator(queue)
        if callable(account_changed):
            stream = account_changed()
            if hasattr(stream, "__anext__"):
                return cast(AsyncIterator[object], stream)
        return None

    def _event_label(self, event: OrderEventMessage) -> str:
        context = _stream_context.get()
        if context is not None:
            return context.label
        payload = _parse_raw_payload_best_effort(event.raw_payload)
        if isinstance(payload, dict):
            label = payload.get("label") or payload.get("gateway_label")
            if label is not None:
                return str(label)
        return "unknown"

    def _observe_lag(self, label: str, broker_event_at: datetime | None) -> None:
        if broker_event_at is None:
            return
        event_at = broker_event_at
        if event_at.tzinfo is None:
            event_at = event_at.replace(tzinfo=UTC)
        lag_ms = max((datetime.now(UTC) - event_at).total_seconds() * 1000, 0)
        metrics.broker_order_event_lag_ms.labels(label=label).observe(lag_ms)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass


async def _queue_iterator(queue: _AccountEventQueue) -> AsyncIterator[object]:
    while True:
        yield await queue.get()


def _coerce_account_changed_event(event: object) -> AccountChangedEvent | None:
    if isinstance(event, AccountChangedEvent):
        return event
    if isinstance(event, dict):
        kind = event.get("kind")
        label = event.get("label") or event.get("gateway_label")
        account_number = (
            event.get("account_number") or event.get("account") or event.get("acctNumber")
        )
        account_id = event.get("account_id")
    else:
        kind = getattr(event, "kind", None)
        label = getattr(event, "label", None) or getattr(event, "gateway_label", None)
        account_number = getattr(event, "account_number", None)
        account_id = getattr(event, "account_id", None)
    if kind is None or label is None or account_number is None:
        return None
    return AccountChangedEvent(
        kind=str(kind),
        label=str(label),
        account_number=str(account_number),
        account_id=UUID(str(account_id)) if account_id is not None else None,
    )


def _normalize_status(status: str) -> str:
    normalized = status.lower()
    if normalized.startswith("status_"):
        normalized = normalized.removeprefix("status_")
    aliases = {
        "pending": "pending_submit",
        "submitted": "submitted",
        "partial": "partial",
        "filled": "filled",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "rejected": "rejected",
        "expired": "expired",
        "inactive": "inactive",
        "modified": "modified",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown order status {status!r}") from exc


def _parse_decimal(value: str, *, field: str) -> Decimal | None:
    if value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field}") from exc


def _parse_client_order_id(value: str) -> UUID | None:
    if value == "":
        return None
    return UUID(value)


def _parse_raw_payload(raw_payload: str) -> dict[str, Any] | list[Any] | None:
    if raw_payload == "":
        return None
    parsed = json.loads(raw_payload)
    if isinstance(parsed, dict) or isinstance(parsed, list):
        return parsed
    return {"value": parsed}


def _parse_raw_payload_best_effort(raw_payload: str) -> object:
    try:
        return _parse_raw_payload(raw_payload)
    except json.JSONDecodeError:
        return {"raw": raw_payload}
