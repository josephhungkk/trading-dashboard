"""Phase 8a - adaptive OrderPoller per (gateway_label, account_id) - CRIT-1 supervisor key."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from sidecar_schwab import metrics
from sidecar_schwab.client import SchwabHTTPError
from sidecar_schwab.normalize import schwab_status_to_wire, schwab_to_wire_order
from sidecar_schwab.order_state_cache import OrderState, OrderStateCache

logger = structlog.get_logger(__name__)

_FAST_TICK_S = 2.0
_IDLE_TICK_S = 30.0
_MAX_BACKOFF_S = 30.0
_MAX_QUEUE_SIZE = 1000  # bounded queue per Codex pattern D


def compute_backoff(attempt: int) -> float:
    """Exponential backoff capped at _MAX_BACKOFF_S. attempt=0 -> 2s."""
    return min(2.0 * (2**attempt), _MAX_BACKOFF_S)


@dataclass
class WireEvent:
    broker_order_id: str
    client_order_id: str
    kind: str
    status: str
    exec_id: str = ""


class _FanOut:
    """Per-callback isolated fan-out (Codex C). Bounded queues (D)."""

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[WireEvent | None]] = []

    def subscribe(self) -> asyncio.Queue[WireEvent | None]:
        q: asyncio.Queue[WireEvent | None] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WireEvent | None]) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    async def publish(self, ev: WireEvent) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Drop slow consumer (Codex D bounded-queue rule).
                self.unsubscribe(q)
                metrics.SCHWAB_FANOUT_SUBSCRIBER_DROPPED_TOTAL.inc()


class OrderPoller:
    def __init__(
        self,
        *,
        client,
        state_cache: OrderStateCache,
        gateway_label: str,
        account_id: str,
        account_hash_resolver: Callable[[], str],
    ) -> None:
        self._client = client
        self._state = state_cache
        self._gw = gateway_label
        self._aid = account_id
        self._hash_resolver = account_hash_resolver
        self._tick = _IDLE_TICK_S
        self._backoff_attempt = 0
        self._fan_out = _FanOut()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_poll_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        self._in_flight: set[str] = set()

    def fan_out(self) -> _FanOut:
        return self._fan_out

    def activate_fast(self) -> None:
        if self._tick != _FAST_TICK_S:
            metrics.SCHWAB_ORDER_POLLER_CADENCE_CHANGED_TOTAL.labels(
                gateway_label=self._gw,
                account_id=self._aid,
                from_cadence=str(self._tick),
                to_cadence=str(_FAST_TICK_S),
            ).inc()
            self._tick = _FAST_TICK_S

    def _mark_no_in_flight(self) -> None:
        if not self._in_flight and self._tick != _IDLE_TICK_S:
            metrics.SCHWAB_ORDER_POLLER_CADENCE_CHANGED_TOTAL.labels(
                gateway_label=self._gw,
                account_id=self._aid,
                from_cadence=str(self._tick),
                to_cadence=str(_IDLE_TICK_S),
            ).inc()
            self._tick = _IDLE_TICK_S

    def current_tick_seconds(self) -> float:
        return self._tick

    async def start(self) -> None:
        await self._state.hydrate()
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name=f"poller:{self._gw}:{self._aid}"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)  # Codex B
            self._task = None

    async def handle_account_hash_rotation(self) -> None:
        await self._state.invalidate_all()
        self._in_flight.clear()
        self._last_poll_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                events = await self._poll_once()
                for ev in events:
                    await self._fan_out.publish(ev)
                self._backoff_attempt = 0
                self._mark_no_in_flight()
            except SchwabHTTPError as exc:
                if exc.status_code == 429:
                    self._backoff_attempt += 1
                    metrics.SCHWAB_ORDER_POLLER_ITERATIONS_TOTAL.labels(
                        gateway_label=self._gw,
                        account_id=self._aid,
                        cadence=f"backoff_{self._backoff_attempt}",
                    ).inc()
                    await asyncio.sleep(compute_backoff(self._backoff_attempt))
                    continue
                logger.error(
                    "schwab_poller_http_error",
                    status=exc.status_code,
                    endpoint=exc.endpoint,
                )
            except (OSError, TimeoutError) as exc:
                logger.error("schwab_poller_transport_error", error=str(exc))
            metrics.SCHWAB_ORDER_POLLER_ITERATIONS_TOTAL.labels(
                gateway_label=self._gw,
                account_id=self._aid,
                cadence=f"{self._tick:g}s",
            ).inc()
            await asyncio.sleep(self._tick)

    async def _poll_once(self) -> list[WireEvent]:
        account_hash = self._hash_resolver()
        await self._client.ensure_fresh_token()
        since = self._last_poll_iso
        rows = await self._client.get_orders_since(account_hash, since_iso=since)
        self._last_poll_iso = datetime.now(UTC).isoformat()

        events: list[WireEvent] = []
        for raw in rows:
            coid = raw.get("clientOrderId") or ""
            if not coid:
                continue
            normalized = schwab_to_wire_order(raw, client_order_id=coid)
            prev = await self._state.get(coid)
            prev_exec_ids: set[str] = prev.last_exec_ids if prev is not None else set()

            # CRIT-3: accumulate all exec_ids seen this poll into the running set so
            # that fills 1..N-1 from a multi-fill batch are not re-emitted next poll.
            new_exec_ids: set[str] = prev_exec_ids | {
                f.exec_id for f in normalized.fills if f.exec_id
            }
            # Cap to _MAX_EXEC_IDS entries to bound memory for very active orders.
            from sidecar_schwab.order_state_cache import _MAX_EXEC_IDS  # noqa: PLC0415
            if len(new_exec_ids) > _MAX_EXEC_IDS:
                new_exec_ids = set(sorted(new_exec_ids)[-_MAX_EXEC_IDS:])

            new_state = OrderState(
                client_order_id=coid,
                broker_order_id=normalized.broker_order_id,
                schwab_status=raw["status"],
                entered_time_iso=normalized.entered_time_iso,
                last_exec_ids=new_exec_ids,
            )
            if prev is None:
                events.append(
                    WireEvent(
                        broker_order_id=normalized.broker_order_id,
                        client_order_id=coid,
                        kind="status",
                        status="submitted",
                    )
                )
            elif prev.schwab_status != new_state.schwab_status:
                mapping = schwab_status_to_wire(new_state.schwab_status)
                events.append(
                    WireEvent(
                        broker_order_id=normalized.broker_order_id,
                        client_order_id=coid,
                        kind=mapping.kind,
                        status=mapping.wire_status,
                    )
                )
            for fill in normalized.fills:
                # CRIT-3: check against the full prev set, not just the last id.
                if fill.exec_id and fill.exec_id not in prev_exec_ids:
                    events.append(
                        WireEvent(
                            broker_order_id=normalized.broker_order_id,
                            client_order_id=coid,
                            kind="fill",
                            status="submitted",
                            exec_id=fill.exec_id,
                        )
                    )
            await self._state.put(new_state)
            mapping = schwab_status_to_wire(new_state.schwab_status)
            if mapping.terminal:
                self._in_flight.discard(coid)
            else:
                self._in_flight.add(coid)
                self.activate_fast()
            metrics.SCHWAB_ORDER_EVENT_EMITTED_TOTAL.labels(kind=mapping.kind).inc()
        return events
