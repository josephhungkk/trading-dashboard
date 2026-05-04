"""SubscriptionRegistry — refcount + per-WS cap + global cap + rate-limit.

Phase 7b.1 HIGH-6 mitigation. Owns the in-memory subscription state for the
streaming-quote engine:

* per-WS sets: each WS connection's currently-subscribed canonical_ids.
* global refcounts: sum across all WSes for each canonical_id; the engine
  consults this to decide whether an upstream sidecar subscription should
  start (0→1) or stop (1→0).
* per-WS rate-limit window (60 s sliding via deque) — protects against a
  runaway client flooding subscribe/unsubscribe ops.
* per-canonical_id route assignment, written by the SourceRouter (Task B3)
  and read by the engine when fanning out diffs to the right SidecarStream.

All state mutations happen under one ``asyncio.Lock`` — this matches the
single-worker default for Phase 7b.1 (multi-worker is Phase 24, where the
state moves to Redis).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from app.core.metrics import QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL
from app.services.quotes.base import CanonicalId, SourceId

WSConnId = UUID

RATE_WINDOW_SECONDS: float = 60.0


@dataclass(slots=True)
class SubscribeDiff:
    """Result of an :meth:`SubscriptionRegistry.add` call.

    ``added`` is the set whose global refcount transitioned 0→1 (the engine
    should start an upstream subscription for each). ``rejected`` is the set
    that hit a cap or rate-limit; ``rejected_reason`` is the *first* cap kind
    encountered (cap_per_ws / cap_global / rate_limit) and is ``None`` only
    when every requested symbol was accepted.
    """

    added: set[CanonicalId] = field(default_factory=set)
    rejected: set[CanonicalId] = field(default_factory=set)
    rejected_reason: str | None = None


@dataclass(slots=True)
class UnsubscribeDiff:
    """Result of an :meth:`SubscriptionRegistry.remove` /
    :meth:`SubscriptionRegistry.remove_ws` call.

    ``removed`` is the set whose global refcount transitioned 1→0 (the engine
    should stop the upstream subscription).
    """

    removed: set[CanonicalId] = field(default_factory=set)


class SubscriptionRegistry:
    """In-memory subscription bookkeeping with caps + rate-limit."""

    def __init__(
        self,
        *,
        cap_per_ws: int,
        cap_global: int,
        sub_rate_limit_per_minute: int,
    ) -> None:
        self._cap_per_ws = cap_per_ws
        self._cap_global = cap_global
        self._rate_limit_per_minute = sub_rate_limit_per_minute

        self._per_ws: dict[WSConnId, set[CanonicalId]] = defaultdict(set)
        self._global_refs: dict[CanonicalId, int] = defaultdict(int)
        self._routes: dict[CanonicalId, SourceId | str] = {}
        self._rate_buckets: dict[WSConnId, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    # ── add / remove ──────────────────────────────────────────────────────

    async def add(
        self,
        ws: WSConnId,
        symbols: Iterable[str],
    ) -> SubscribeDiff:
        diff = SubscribeDiff()
        async with self._lock:
            now = time.monotonic()
            self._evict_rate_window(ws, now)
            ws_set = self._per_ws[ws]

            for sym_raw in symbols:
                sym = CanonicalId(sym_raw)

                if sym in ws_set:
                    continue  # idempotent — already counted

                if len(ws_set) >= self._cap_per_ws:
                    diff.rejected.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "cap_per_ws"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(cap_kind="cap_per_ws").inc()
                    continue

                if len(self._global_refs) >= self._cap_global and sym not in self._global_refs:
                    diff.rejected.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "cap_global"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(cap_kind="cap_global").inc()
                    continue

                if len(self._rate_buckets[ws]) >= self._rate_limit_per_minute:
                    diff.rejected.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "rate_limit"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(cap_kind="rate_limit").inc()
                    continue

                ws_set.add(sym)
                prev = self._global_refs[sym]
                self._global_refs[sym] = prev + 1
                if prev == 0:
                    diff.added.add(sym)
                self._rate_buckets[ws].append(now)

        return diff

    async def remove(
        self,
        ws: WSConnId,
        symbols: Iterable[str],
    ) -> UnsubscribeDiff:
        diff = UnsubscribeDiff()
        async with self._lock:
            ws_set = self._per_ws.get(ws)
            if not ws_set:
                return diff

            for sym_raw in symbols:
                sym = CanonicalId(sym_raw)
                if sym not in ws_set:
                    continue
                ws_set.discard(sym)
                self._decrement_locked(sym, diff)

            if not ws_set:
                self._per_ws.pop(ws, None)
        return diff

    async def remove_ws(self, ws: WSConnId) -> UnsubscribeDiff:
        diff = UnsubscribeDiff()
        async with self._lock:
            ws_set = self._per_ws.pop(ws, None)
            self._rate_buckets.pop(ws, None)
            if not ws_set:
                return diff
            for sym in ws_set:
                self._decrement_locked(sym, diff)
        return diff

    def _decrement_locked(
        self,
        sym: CanonicalId,
        diff: UnsubscribeDiff,
    ) -> None:
        prev = self._global_refs.get(sym, 0)
        if prev <= 1:
            self._global_refs.pop(sym, None)
            self._routes.pop(sym, None)
            diff.removed.add(sym)
        else:
            self._global_refs[sym] = prev - 1

    def _evict_rate_window(self, ws: WSConnId, now: float) -> None:
        """Caller must hold :attr:`_lock`. Drops timestamps older than 60 s."""
        bucket = self._rate_buckets.get(ws)
        if not bucket:
            return
        cutoff = now - RATE_WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    # ── routing ───────────────────────────────────────────────────────────

    def set_route(self, canonical_id: str, source_id: SourceId | str) -> None:
        """Set the upstream source for a canonical_id. No lock — single-writer
        contract: only the SourceRouter's reroute task calls this."""
        self._routes[CanonicalId(canonical_id)] = source_id

    def get_route(self, canonical_id: str) -> SourceId | str | None:
        return self._routes.get(CanonicalId(canonical_id))

    # ── read accessors ────────────────────────────────────────────────────

    def get_active(self) -> set[CanonicalId]:
        """Snapshot of every globally-active canonical_id."""
        return set(self._global_refs.keys())

    def get_active_for(self, source: SourceId | str) -> set[CanonicalId]:
        """Snapshot of canonical_ids currently routed to ``source``."""
        return {sym for sym, src in self._routes.items() if src == source}

    def per_ws_count(self, ws: WSConnId) -> int:
        return len(self._per_ws.get(ws, set()))

    def global_count(self) -> int:
        return len(self._global_refs)
