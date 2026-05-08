"""QuoteEngine — central tick fan-in + invariants INV-Q-1..4 (Phase 7b.1 B5).

Pulls together:

* :class:`InstrumentResolver` (A4) — first-observation creates
  ``instruments`` + ``symbol_aliases`` rows.
* :class:`SubscriptionRegistry` (B2) — refcount + caps + rate-limit.
* :class:`SourceRouter` (B3) — config-driven priority + health window.
* :class:`SidecarStream` (B4) — per-source bidi gRPC quote feed.

Owns the ``_on_quote`` callback wired into every SidecarStream — it is the
single chokepoint where engine invariants are enforced:

* **INV-Q-1 (single-worker loopback suppression)** — in single-worker mode
  (``uvicorn --workers 1``, the 7b.1 default), the engine **publishes to
  Redis** (so Phase-24 multi-worker peers can subscribe) **but does NOT
  subscribe to its own publishes**. ``self._subscriber_task is None``
  asserts this. The in-process ``_notify_conflators`` is the only delivery
  path to local conflators.
* **INV-Q-2 (boundary strip — MED-2)** — engine's first action in
  ``_on_quote`` is ``q.raw_payload = b""`` unless ``OPERATOR_TRACE_QUOTES=1``
  is set on the backend. Stripping happens before cache, before publish,
  before notify — so audit consumers, future bots, and FE never see
  internal sidecar bytes.
* **INV-Q-3 (staleness signals do NOT drive reroute)** — per-symbol
  staleness is a UI signal only. Reroute decisions consult source-aggregate
  health (delegated to :class:`SourceRouter.compute_health_state`). The
  engine never calls ``router.reroute`` from a stale-flag emission path.
* **INV-Q-4 (token rotation reconnect within 2 s)** — ``request_token_rotation``
  forwards to the source's :class:`SidecarStream.request_reconnect`, which
  sets the ``_token_rotation`` Event; the run loop's inner wait races
  against that event so the next reconnect happens within the asyncio
  scheduler's next tick (~ms in tests, well under the 2 s spec budget).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID, uuid4

import structlog
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app._generated.broker.v1 import broker_pb2 as pb
from app.core.metrics import (
    QUOTE_CACHE_SIZE,
    QUOTE_CONFLATOR_NOTIFY_FAILURES_TOTAL,
    QUOTE_ENGINE_TICKS_TOTAL,
    QUOTE_REDIS_PUBLISH_FAILURES_TOTAL,
)
from app.models.instruments import Instrument
from app.services.quotes.base import CanonicalId
from app.services.quotes.registry import (
    SubscribeDiff,
    SubscriptionRegistry,
    UnsubscribeDiff,
    WSConnId,
)
from app.services.quotes.router import SourceRouter
from app.services.quotes.upstream.sidecar_stream import SidecarStream

_CACHE_TTL_SECONDS: float = 60.0
_OPERATOR_TRACE_ENV: str = "OPERATOR_TRACE_QUOTES"

ConflatorCallback = Callable[[pb.QuoteMessage], Awaitable[None] | None]

_log = structlog.get_logger(__name__)


def _route_as_str(route: object) -> str | None:
    """Coerce a route value (``SourceId | str | None``) to ``str | None``.

    The registry stores routes as ``SourceId | str``; Phase 7b.1 callers
    treat them as plain strings.
    """
    if route is None:
        return None
    return str(route)


class QuoteEngine:
    """Tick fan-in + sub/unsub orchestrator."""

    def __init__(
        self,
        *,
        registry: SubscriptionRegistry,
        router: SourceRouter,
        redis: Redis,
        streams: dict[str, SidecarStream] | None = None,
        publisher_worker_id: UUID | None = None,
        single_worker: bool = True,
        db_factory: async_sessionmaker[Any] | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._redis = redis
        self._streams: dict[str, SidecarStream] = streams or {}
        self._publisher_worker_id = publisher_worker_id or uuid4()
        # ``single_worker`` is informational only today — Phase 24 will key
        # ``_subscriber_task`` startup on it. Stored as a public-readable
        # attribute so the lifespan wiring can assert the contract.
        self.single_worker: bool = single_worker
        # CRIT-2 fix: factory for DB sessions used to resolve Instrument rows
        # during route assignment. None in unit tests that mock the router.
        self._db_factory: async_sessionmaker[Any] | None = db_factory

        # INV-Q-1: subscriber task is None in single-worker mode. When
        # ``single_worker=False`` lands in Phase 24 this becomes the
        # Redis-pubsub consumer task spawned in ``start()``.
        self._subscriber_task: asyncio.Task[None] | None = None

        self._cache: dict[CanonicalId, tuple[pb.QuoteMessage, float]] = {}
        self._conflators: dict[WSConnId, ConflatorCallback] = {}
        self._conflator_subs: dict[WSConnId, set[CanonicalId]] = {}

        self._stream_tasks: list[asyncio.Task[None]] = []

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._stream_tasks:
            # Idempotent: a duplicate start() must not double-spawn stream
            # tasks (would double-deliver every tick).
            raise RuntimeError("QuoteEngine.start() already called")
        for stream in self._streams.values():
            self._stream_tasks.append(asyncio.create_task(stream.run()))

    async def stop(self) -> None:
        for stream in self._streams.values():
            stream.stop()
        for t in self._stream_tasks:
            t.cancel()
        if self._stream_tasks:
            results = await asyncio.gather(*self._stream_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    _log.warning("quote_engine.stream_task_exit_exception", error=repr(result))
        self._stream_tasks.clear()
        # HIGH fix: close gRPC channels after tasks are joined so no new RPCs
        # can be started on a closing channel.
        for stream in self._streams.values():
            await stream.close_channel()

    # ── conflator registration ────────────────────────────────────────────

    def register_conflator(self, ws: WSConnId, callback: ConflatorCallback) -> None:
        self._conflators[ws] = callback
        self._conflator_subs.setdefault(ws, set())

    def unregister_conflator(self, ws: WSConnId) -> None:
        self._conflators.pop(ws, None)
        self._conflator_subs.pop(ws, None)

    # ── subscribe / unsubscribe ────────────────────────────────────────────

    async def subscribe(self, ws: WSConnId, symbols: Iterable[str]) -> SubscribeDiff:
        diff = await self._registry.add(ws, symbols)
        if not diff.added:
            return diff

        self._conflator_subs.setdefault(ws, set()).update(diff.added)

        # CRIT-2 fix: assign source routes via SourceRouter for newly-added
        # symbols (0→1 refcount transitions). Without this, get_route() always
        # returns None and _group_by_source() produces an empty dict, so no
        # SidecarStream ever receives subscriptions → no quotes flow.
        await self._assign_routes(diff.added)

        # Group accepted symbols by source via registry routes; only newly-
        # added canonical_ids cross the wire to the sidecar.
        per_source = self._group_by_source(diff.added)
        for source, canonicals in per_source.items():
            stream = self._streams.get(source)
            if stream is not None:
                await stream.add(canonicals)

        return diff

    async def _assign_routes(self, canonical_ids: Iterable[str]) -> None:
        """Resolve source route for each canonical_id and write to registry.

        Looks up the :class:`Instrument` row from the DB (needed by
        :class:`SourceRouter.route`), then calls
        :meth:`SubscriptionRegistry.set_route`. Symbols with no DB row or no
        healthy source are left un-routed — they will not forward to any
        sidecar this tick, but will be retried on the next subscribe call.
        Silently skips if no ``db_factory`` was supplied (unit-test mode).
        """
        if self._db_factory is None:
            return
        async with self._db_factory() as session:
            for canonical_id in canonical_ids:
                instrument = await self._lookup_instrument(session, canonical_id)
                if instrument is None:
                    _log.warning(
                        "quote_engine.subscribe.instrument_not_found",
                        canonical_id=canonical_id,
                    )
                    continue
                source = await self._router.route(instrument)
                if source is None:
                    _log.warning(
                        "quote_engine.subscribe.no_source",
                        canonical_id=canonical_id,
                    )
                    continue
                self._registry.set_route(canonical_id, source)

    @staticmethod
    async def _lookup_instrument(
        session: object,
        canonical_id: str,
    ) -> Instrument | None:
        """Fetch the :class:`Instrument` row for ``canonical_id``. Returns
        ``None`` if the row does not exist (symbol never seeded)."""
        from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

        if not isinstance(session, _AsyncSession):
            return None  # pragma: no cover — only reached in test mode
        result = await session.execute(
            select(Instrument).where(Instrument.canonical_id == canonical_id)
        )
        return result.scalar_one_or_none()

    async def unsubscribe(self, ws: WSConnId, symbols: Iterable[str]) -> UnsubscribeDiff:
        # Snapshot routes BEFORE registry.remove — _decrement_locked drops
        # the route on the 1→0 transition, so a post-remove lookup would
        # miss every removed symbol's source.
        symbol_list = list(symbols)
        pre_routes: dict[str, str | None] = {
            str(s): _route_as_str(self._registry.get_route(s)) for s in symbol_list
        }

        diff = await self._registry.remove(ws, symbol_list)
        ws_subs = self._conflator_subs.get(ws)
        if ws_subs is not None:
            ws_subs.difference_update(diff.removed)

        per_source = self._group_by_source_with(diff.removed, pre_routes)
        for source, canonicals in per_source.items():
            stream = self._streams.get(source)
            if stream is not None:
                await stream.remove(canonicals)
        return diff

    async def disconnect_ws(self, ws: WSConnId) -> UnsubscribeDiff:
        # Snapshot routes for everything this WS held BEFORE registry mutation.
        ws_subs_snapshot = set(self._conflator_subs.get(ws, set()))
        pre_routes: dict[str, str | None] = {
            str(s): _route_as_str(self._registry.get_route(s)) for s in ws_subs_snapshot
        }

        diff = await self._registry.remove_ws(ws)
        self._conflators.pop(ws, None)
        self._conflator_subs.pop(ws, None)

        per_source = self._group_by_source_with(diff.removed, pre_routes)
        for source, canonicals in per_source.items():
            stream = self._streams.get(source)
            if stream is not None:
                await stream.remove(canonicals)
        return diff

    def _group_by_source(self, canonicals: Iterable[str]) -> dict[str, list[str]]:
        """Live route lookup — safe for ``subscribe`` paths where routes
        persist (refcount stays >0 across the call). Do NOT use from
        ``unsubscribe`` / ``disconnect_ws`` — see ``_group_by_source_with``.
        """
        grouped: dict[str, list[str]] = {}
        for canonical in canonicals:
            source = self._registry.get_route(canonical)
            if source is None:
                continue
            grouped.setdefault(str(source), []).append(canonical)
        return grouped

    @staticmethod
    def _group_by_source_with(
        canonicals: Iterable[str],
        pre_routes: dict[str, str | None],
    ) -> dict[str, list[str]]:
        """Variant that uses a pre-captured route map — used by remove paths
        because :meth:`SubscriptionRegistry._decrement_locked` pops the route
        on the last unsubscribe transition, so a post-remove lookup would
        return ``None`` for every removed symbol.
        """
        grouped: dict[str, list[str]] = {}
        for canonical in canonicals:
            source = pre_routes.get(canonical)
            if source is None:
                continue
            grouped.setdefault(str(source), []).append(canonical)
        return grouped

    # ── tick path (INV-Q-1, INV-Q-2) ──────────────────────────────────────

    async def _on_quote(self, q: pb.QuoteMessage) -> None:
        # INV-Q-2: zero raw_payload at the engine boundary (audit consumers,
        # bots, FE, and Redis bus all see empty bytes by default). Operator
        # opt-in via OPERATOR_TRACE_QUOTES=1.
        if os.environ.get(_OPERATOR_TRACE_ENV) != "1":
            q.raw_payload = b""

        QUOTE_ENGINE_TICKS_TOTAL.labels(source=q.source).inc()

        canonical = CanonicalId(q.canonical_id)
        self._cache[canonical] = (q, time.monotonic())
        QUOTE_CACHE_SIZE.set(len(self._cache))

        # Publish to Redis bus with publisher_worker_id envelope. Single-worker
        # mode does not subscribe to its own publishes (INV-Q-1); the bus is
        # only useful when multi-worker (Phase 24) lights up.
        try:
            envelope = {
                "v": 1,
                "publisher_worker_id": str(self._publisher_worker_id),
                "q": MessageToDict(q, preserving_proto_field_name=True),
            }
            channel = f"quote.{q.source}.{q.canonical_id}"
            await self._redis.publish(channel, json.dumps(envelope))
        except Exception:
            _log.exception("quote_engine.redis_publish_failed", source=q.source)
            QUOTE_REDIS_PUBLISH_FAILURES_TOTAL.inc()

        # In-process: fan out to every conflator subscribed to this canonical.
        await self._notify_conflators(canonical, q)

    async def _notify_conflators(self, canonical: CanonicalId, q: pb.QuoteMessage) -> None:
        """Fan ``q`` out to every conflator subscribed to ``canonical``.

        Takes an atomic snapshot of both ``_conflator_subs`` and
        ``_conflators`` (synchronous; under CPython GIL no other coroutine
        can interleave between the two ``dict()`` copies) so concurrent
        ``disconnect_ws`` / ``unregister_conflator`` cannot leave us with
        a stale (ws, callback) pair mid-iteration. Per-conflator failures
        are caught + counted; one bad callback never tears down the whole
        tick path.
        """
        subs_snapshot = dict(self._conflator_subs)
        cbs_snapshot = dict(self._conflators)
        targets: list[tuple[WSConnId, ConflatorCallback]] = []
        for ws, subs in subs_snapshot.items():
            if canonical in subs:
                cb = cbs_snapshot.get(ws)
                if cb is not None:
                    targets.append((ws, cb))

        for _, cb in targets:
            try:
                maybe_awaitable = cb(q)
                if maybe_awaitable is not None:
                    await maybe_awaitable
            except Exception:
                _log.exception("quote_engine.conflator_notify_failed")
                QUOTE_CONFLATOR_NOTIFY_FAILURES_TOTAL.inc()

    # ── cache + accessor helpers ─────────────────────────────────────────

    def get_cached(self, canonical_id: str) -> pb.QuoteMessage | None:
        entry = self._cache.get(CanonicalId(canonical_id))
        if entry is None:
            return None
        q, ts = entry
        if time.monotonic() - ts > _CACHE_TTL_SECONDS:
            self._cache.pop(CanonicalId(canonical_id), None)
            QUOTE_CACHE_SIZE.set(len(self._cache))
            return None
        return q

    def is_subscriber_task_running(self) -> bool:
        """INV-Q-1 assertion helper for tests."""
        return self._subscriber_task is not None and not self._subscriber_task.done()

    @property
    def publisher_worker_id(self) -> UUID:
        return self._publisher_worker_id

    @property
    def streams(self) -> dict[str, SidecarStream]:
        return self._streams

    # ── operator hooks ────────────────────────────────────────────────────

    def request_token_rotation(self, source: str) -> None:
        """INV-Q-4 plumbing — operator/admin notifies that a token has been
        rotated; engine forwards to the source's :class:`SidecarStream`."""
        stream = self._streams.get(source)
        if stream is None:
            return
        stream.request_reconnect()


__all__ = ["ConflatorCallback", "QuoteEngine"]
