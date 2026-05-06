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


@dataclass
class OcoOrchestrator:
    """Single-leader OCO orchestrator backed by Redis advisory lock.

    ``db`` is an ``async_sessionmaker[AsyncSession]`` in production; in tests
    pass a factory function/AsyncMock that follows the same async-context-manager
    protocol (yields a session with ``.execute()`` and ``.commit()``).
    ``redis`` must satisfy the ``_RedisLike`` protocol; pass AsyncMock in tests.
    """

    db: async_sessionmaker[AsyncSession]  # type: ignore[type-arg]  # tests inject compatible mock
    redis: _RedisLike
    _active: dict[str, dict] = field(default_factory=dict, init=False)
    _streams: dict[tuple[str, str], asyncio.Task[None]] = field(default_factory=dict, init=False)
    _stream_last_pending: dict[tuple[str, str], float] = field(default_factory=dict, init=False)
    _leader: bool = field(default=False, init=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)
    _clock: Any = field(default=None, init=False)  # injected for tests; defaults to time.monotonic

    def __post_init__(self) -> None:
        if self._clock is None:
            self._clock = time.monotonic

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

    async def process_fill_event(self, broker_id: str, order_id: str, fill_data: dict) -> None:
        """Handle an order fill: transition state and cancel the sibling leg."""
        if not self._leader:
            return  # follower; leader will pick this up via DB
        link = self._find_link(broker_id, order_id)
        if link is None or link["status"] in TERMINAL_STATUSES:
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

    async def _transition(
        self, link: dict, new_status: str, failure_reason: str | None = None
    ) -> None:
        """Persist a status transition for an oco_link row."""
        if link["status"] in TERMINAL_STATUSES:
            raise ValueError(f"invalid transition from terminal {link['status']} to {new_status}")
        async with self.db() as session:
            await session.execute(
                text(
                    "UPDATE oco_links SET status=:s, failure_reason=:fr, "
                    "updated_at=NOW() WHERE id=:id"
                ),
                {"s": new_status, "fr": failure_reason, "id": link["id"]},
            )
            await session.commit()
        link["status"] = new_status
        link["failure_reason"] = failure_reason
        log.info("oco_orchestrator.transition", link_id=str(link["id"]), status=new_status)

    def _find_link(self, broker_id: str, order_id: str) -> dict | None:
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
