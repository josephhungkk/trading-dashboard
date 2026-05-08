"""OCO orchestrator -- single-leader service that watches OCO link fills and cancels siblings.

Single-instance via Redis advisory lock. Followers stay idle and let the leader
process fill events. State machine drives oco_links rows through 9 statuses.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

LOCK_KEY = "oco:advisory_lock"
LOCK_TOKEN = "oco:leader"  # value stored in the lock key for ownership checks
LOCK_TTL_SECONDS = 60
RENEW_SECONDS = 30
MAX_STREAMS = 100
IDLE_STREAM_SECONDS = 60

TERMINAL_STATUSES = frozenset({"COMPLETED", "CANCELED", "ERROR", "CANCEL_FAILED"})


class _RedisLike(Protocol):
    """Minimal Redis interface used by OcoOrchestrator; compatible with AsyncMock."""

    async def set(
        self, name: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> Any: ...
    async def get(self, name: str) -> Any: ...
    async def expire(self, name: str, time: int) -> Any: ...
    async def delete(self, name: str) -> Any: ...


class CapacityError(RuntimeError):
    """Raised when MAX_STREAMS would be exceeded."""


@dataclass
class OcoOrderResponse:
    """Response from a native broker OCO placement."""

    external_order_id: str
    leg_order_ids: list[str]


@dataclass
class AlpacaOcoOrderRequest:
    """Minimal native Alpaca OCO request shape consumed by the Alpaca client."""

    symbol: str
    qty: Decimal
    side: str
    time_in_force: str
    order_class: str
    limit_price: Decimal
    stop_price: Decimal
    stop_limit_price: Decimal | None
    asset_class: str


def oco_group_id_for_ibkr(oco_link_id: uuid.UUID) -> str:
    """Build a deterministic OCA group ID for IBKR (max 32 chars).

    IBKR's TWS API ocaGroup is a string identifier; max 32 chars. We derive
    it deterministically from the oco_link UUID so cross-process replay yields
    the same group.
    """
    raw = f"OCO-{oco_link_id.hex[:24]}"
    if len(raw) > 32:
        raise AssertionError(f"oca group id too long: {raw}")
    return raw


async def dispatch_oco_alpaca_equity(request: Any, alpaca_client: Any) -> OcoOrderResponse:
    """Submit an Alpaca equity OCO using Alpaca's native order_class support."""
    return await _dispatch_oco_alpaca_native(request, alpaca_client)


async def dispatch_oco_alpaca_crypto(
    request: Any,
    alpaca_client: Any,
    *,
    # Default FALSE: crypto OCO is empirically unconfirmed (spec §6, Alembic 0022).
    # Caller must pass crypto_oco_supported=True only after T-O.5 PASS branch confirms.
    crypto_oco_supported: bool = False,
) -> OcoOrderResponse:
    """Dispatch crypto OCO only when empirical support has been explicitly enabled."""
    if not crypto_oco_supported:
        raise NotImplementedError("alpaca_crypto_oco_not_supported")
    return await _dispatch_oco_alpaca_native(request, alpaca_client)


async def _dispatch_oco_alpaca_native(
    request: Any,
    alpaca_client: Any,
) -> OcoOrderResponse:
    """Shared OCO submission path for equity and crypto.

    Wire shape matches the empirically-validated paper script
    (scripts/empirical/alpaca_equity_oco_paper.py): LimitOrderRequest with
    order_class=OCO, limit_price for the take-profit leg, stop_price for the
    stop-loss trigger. No nested TakeProfitRequest/StopLossRequest objects —
    Alpaca's OCO contract differs from BRACKET in that the parent fields ARE
    the legs (chunk-OCO spec H-2).

    Lazy alpaca-py import: backend does not ship alpaca-py as a runtime dep
    (sidecar_alpaca owns the SDK). Production callers must run inside an
    image that has alpaca-py installed.
    """
    from alpaca.trading.enums import (  # type: ignore[import-not-found]
        OrderClass,
        OrderSide,
        TimeInForce,
    )
    from alpaca.trading.requests import LimitOrderRequest  # type: ignore[import-not-found]

    side = request.side.upper()
    tif = request.tif.upper()
    order_data = LimitOrderRequest(
        symbol=request.symbol,
        qty=Decimal(request.qty),
        side=OrderSide[side],
        time_in_force=TimeInForce[tif],
        order_class=OrderClass.OCO,
        limit_price=Decimal(request.limit_price),
        stop_price=Decimal(request.stop_price),
    )
    order = await asyncio.to_thread(alpaca_client.submit_order, order_data=order_data)
    return OcoOrderResponse(
        external_order_id=str(order.id),
        leg_order_ids=[str(leg.id) for leg in getattr(order, "legs", [])],
    )


@dataclass
class OcoOrchestrator:
    """Single-leader OCO orchestrator backed by Redis advisory lock.

    ``db`` is an ``async_sessionmaker[AsyncSession]`` in production; in tests
    pass a factory function/AsyncMock that follows the same async-context-manager
    protocol (yields a session with ``.execute()`` and ``.commit()``).
    ``redis`` must satisfy the ``_RedisLike`` protocol; pass AsyncMock in tests.
    """

    db: async_sessionmaker[AsyncSession]  # tests inject compatible mock
    redis: _RedisLike
    _active: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _streams: dict[tuple[str, str], asyncio.Task[None]] = field(default_factory=dict, init=False)
    _stream_last_pending: dict[tuple[str, str], float] = field(default_factory=dict, init=False)
    _link_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)
    _leader: bool = field(default=False, init=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)
    _clock: Any = field(default=None, init=False)  # injected for tests; defaults to time.monotonic

    def __post_init__(self) -> None:
        if self._clock is None:
            self._clock = time.monotonic

    def _link_lock(self, link_id: str) -> asyncio.Lock:
        """Return (creating if absent) the per-link asyncio.Lock for fill serialisation."""
        lock = self._link_locks.get(link_id)
        if lock is None:
            lock = asyncio.Lock()
            self._link_locks[link_id] = lock
        return lock

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Acquire advisory lock; become leader or enter follower mode."""
        acquired = await self.redis.set(LOCK_KEY, LOCK_TOKEN, ex=LOCK_TTL_SECONDS, nx=True)
        self._leader = bool(acquired)
        if self._leader:
            log.info("oco_orchestrator.leader_acquired")
            self._renewal_task = asyncio.create_task(self._renew_lock())
            await self.hydrate()
        else:
            log.info("oco_orchestrator.follower_mode")

    async def stop(self) -> None:
        """Cancel all tasks and release the lock if leader."""
        self._stopped = True
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(self._renewal_task, return_exceptions=True)
        for task in list(self._streams.values()):
            task.cancel()
        if self._streams:
            await asyncio.gather(*self._streams.values(), return_exceptions=True)
        self._streams.clear()
        if self._leader:
            try:
                await self.redis.delete(LOCK_KEY)
            except (TimeoutError, ConnectionError, OSError, RedisError) as exc:
                log.warning("oco_orchestrator.lock_release_failed", exc=str(exc))

    async def _renew_lock(self) -> None:
        """Periodically extend the advisory lock TTL while leader.

        Verifies ownership before each renewal: if the lock value is no longer
        ``LOCK_TOKEN`` the lock expired and was acquired by another instance, so
        this instance demotes itself to follower and cancels further renewal.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(RENEW_SECONDS)
                try:
                    current = await self.redis.get(LOCK_KEY)
                    if current != LOCK_TOKEN:
                        log.warning(
                            "oco_orchestrator.lock_ownership_lost",
                            current=current,
                        )
                        self._leader = False
                        return
                    await self.redis.expire(LOCK_KEY, LOCK_TTL_SECONDS)
                except (TimeoutError, ConnectionError, OSError, RedisError) as exc:
                    log.warning("oco_orchestrator.lock_renewal_failed", exc=str(exc))
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    async def hydrate(self) -> None:
        """Load all non-terminal oco_links from DB into _active."""
        async with self.db() as session:
            result = await session.execute(
                text(
                    """SELECT id, broker_id, account_id, order_id_a, order_id_b,
                              status, filled_leg_id, failure_reason
                       FROM oco_links
                       WHERE status NOT IN ('COMPLETED', 'CANCELED', 'ERROR', 'CANCEL_FAILED')"""
                )
            )
            rows = result.mappings().all()
        self._active = {str(r["id"]): dict(r) for r in rows}
        log.info("oco_orchestrator.hydrated", count=len(self._active))

    # ------------------------------------------------------------------
    # Fill event processing
    # ------------------------------------------------------------------

    async def process_fill_event(
        self, broker_id: str, order_id: str, fill_data: dict[str, Any]
    ) -> None:
        """Handle an order fill: transition state and cancel the sibling leg.

        HIGH-code-3: per-link asyncio.Lock prevents concurrent fills on the same
        OCO link from racing through the guard check simultaneously.
        """
        if not self._leader:
            return  # follower; leader will pick this up via DB
        link = self._find_link(broker_id, order_id)
        if link is None or link["status"] in TERMINAL_STATUSES:
            return
        async with self._link_lock(str(link["id"])):
            # Re-check status inside the lock — a concurrent fill may have already
            # transitioned to a terminal state while we were waiting.
            if link["status"] in TERMINAL_STATUSES:
                return
            survivor_order_id = (
                link["order_id_b"] if order_id == link["order_id_a"] else link["order_id_a"]
            )
            fill_status = "LEG_A_FILLED" if order_id == link["order_id_a"] else "LEG_B_FILLED"
            await self._transition(link, fill_status)
            cancel_ok = await self._cancel(
                link["broker_id"], str(link["account_id"]), survivor_order_id
            )
            if cancel_ok:
                await self._transition(link, "COMPLETED")
            else:
                await self._transition(
                    link,
                    "CANCEL_FAILED",
                    failure_reason="cancel_rejected: broker rejected sibling cancel",
                )
            # Clean up the lock entry once the link reaches a terminal state.
            if link["status"] in TERMINAL_STATUSES:
                self._link_locks.pop(str(link["id"]), None)

    async def _transition(
        self, link: dict[str, Any], new_status: str, failure_reason: str | None = None
    ) -> None:
        """Persist a status transition for an oco_link row.

        HIGH-code-3: optimistic in-memory update applied BEFORE the async DB
        write so concurrent coroutines see the new status immediately during
        their guard checks, even before the DB round-trip completes.

        HIGH-db-3: DB UPDATE guards on NOT IN terminal statuses — if a second
        concurrent writer already transitioned the row the UPDATE affects 0 rows
        and we log a warning instead of double-applying.
        """
        if link["status"] in TERMINAL_STATUSES:
            raise ValueError(f"invalid transition from terminal {link['status']} to {new_status}")
        # Optimistic in-memory update before the await (HIGH-code-3).
        prev_status = link["status"]
        link["status"] = new_status
        link["failure_reason"] = failure_reason
        async with self.db() as session:
            result = await session.execute(
                text(
                    "UPDATE oco_links SET status=:s, failure_reason=:fr, "
                    "updated_at=NOW() WHERE id=:id "
                    "AND status NOT IN ('COMPLETED','CANCELED','ERROR','CANCEL_FAILED')"
                ),
                {"s": new_status, "fr": failure_reason, "id": link["id"]},
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                log.warning(
                    "oco_orchestrator.transition_lost_race",
                    link_id=str(link["id"]),
                    attempted_status=new_status,
                    prev_status=prev_status,
                )
                return
            await session.commit()
        log.info("oco_orchestrator.transition", link_id=str(link["id"]), status=new_status)

    def _find_link(self, broker_id: str, order_id: str) -> dict[str, Any] | None:
        """Return the oco_link containing order_id for the given broker, or None."""
        for link in self._active.values():
            if link["broker_id"] == broker_id and order_id in (
                link["order_id_a"],
                link["order_id_b"],
            ):
                return link
        return None

    async def _cancel(self, broker_id: str, account_id: str, order_id: str) -> bool:
        """Hook -- override or inject in tests; calls broker sidecar cancel."""
        raise NotImplementedError("subclass or inject _cancel for production wiring")

    # ------------------------------------------------------------------
    # Stream management
    # ------------------------------------------------------------------

    async def _ensure_stream(self, broker_id: str, account_id: str) -> None:
        """Open a fill-event stream for the given pair, capped at MAX_STREAMS."""
        key = (broker_id, account_id)
        if key in self._streams:
            self._stream_last_pending[key] = self._clock()
            return
        if len(self._streams) >= MAX_STREAMS:
            raise CapacityError("oco_orchestrator_capacity_exhausted")
        self._streams[key] = asyncio.create_task(self._stream_order_events(broker_id, account_id))
        self._stream_last_pending[key] = self._clock()

    async def _close_idle_streams(self) -> None:
        """Cancel streams that have had no pending work for IDLE_STREAM_SECONDS."""
        now = self._clock()
        for key in list(self._streams.keys()):
            last = self._stream_last_pending.get(key, now)
            if now - last >= IDLE_STREAM_SECONDS:
                task = self._streams.pop(key)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.gather(task, return_exceptions=True)
                self._stream_last_pending.pop(key, None)

    async def _stream_order_events(self, broker_id: str, account_id: str) -> None:
        """Hook -- subscribe to broker OrderEvent stream and route to process_fill_event."""
        raise NotImplementedError("subclass or inject _stream_order_events for production wiring")


class OcoOrchestratorImpl(OcoOrchestrator):
    """Production subclass that wires _cancel to the broker registry cancel path.

    ``cancel_callable`` must be an async callable with signature:
        (broker_id: str, account_id: str, order_id: str) -> bool

    Instantiated in main.py lifespan to replace the base NotImplementedError stub.
    """

    def __init__(
        self,
        *args: Any,
        cancel_callable: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._cancel_callable = cancel_callable

    async def _cancel(self, broker_id: str, account_id: str, order_id: str) -> bool:
        try:
            return bool(await self._cancel_callable(broker_id, account_id, order_id))
        except Exception as exc:
            log.warning(
                "oco_orchestrator.cancel_failed",
                broker_id=broker_id,
                account_id=account_id,
                order_id=order_id,
                exc=str(exc),
            )
            return False
