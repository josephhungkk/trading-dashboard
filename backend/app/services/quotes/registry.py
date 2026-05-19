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
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from app.core.metrics import QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL
from app.services.quotes.base import CanonicalId, SourceId

WSConnId = UUID | str

RATE_WINDOW_SECONDS: float = 60.0

# Phase 7c CRIT-1 layer 1 — backend-side soft cap per upstream source.
# 5-symbol buffer below Alpaca's free-tier 30 hard cap.
CAP_PER_SOURCE: int = 25

# Phase 7c CRIT-1 / Codex pattern D — bounded refcount table.
MAX_SOURCES: int = 32


@dataclass(slots=True)
class SubscribeDiff:
    """Result of an :meth:`SubscriptionRegistry.add` call.

    * ``added`` — refcount transitioned 0→1; engine starts an upstream sub.
    * ``rejected`` — full set of rejected symbols (union of the per-cap sets
      below).
    * ``rejected_per_ws`` / ``rejected_global`` / ``rejected_rate_limit`` /
      ``rejected_per_source`` — per-cap breakdown so callers can surface a
      different message per cap kind without re-running the cap math.
    * ``rejected_reason`` — the *first* cap encountered in the batch
      (``per_ws`` / ``global`` / ``rate_limit`` / ``per_source``); back-compat
      shorthand for callers that only need a single label. ``None`` iff every
      requested symbol was accepted. Label values match the spec §8.1
      ``quote_subscription_cap_rejected_total{cap_kind}`` set verbatim.
    """

    added: set[CanonicalId] = field(default_factory=set)
    rejected: set[CanonicalId] = field(default_factory=set)
    rejected_per_ws: set[CanonicalId] = field(default_factory=set)
    rejected_global: set[CanonicalId] = field(default_factory=set)
    rejected_rate_limit: set[CanonicalId] = field(default_factory=set)
    rejected_per_source: set[CanonicalId] = field(default_factory=set)
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
        cap_per_ws_override: dict[str, int] | None = None,
    ) -> None:
        self._cap_per_ws = cap_per_ws
        self._cap_global = cap_global
        self._rate_limit_per_minute = sub_rate_limit_per_minute
        self._cap_per_ws_override: dict[str, int] = cap_per_ws_override or {}

        # Plain dicts — defaultdict's read-creates-entry leaks phantom entries
        # on rejected/empty batches (MED fix: _global_refs now plain dict).
        self._per_ws: dict[WSConnId, set[CanonicalId]] = {}
        self._global_refs: dict[CanonicalId, int] = {}
        self._routes: dict[CanonicalId, SourceId | str] = {}
        self._rate_buckets: dict[WSConnId, deque[float]] = {}
        # Phase 7c CRIT-1: per-source refcount, keyed by the SourceRouter-
        # assigned upstream id (e.g. "alpaca"). Bounded at MAX_SOURCES to
        # prevent typo-source key explosion (Pattern D).
        self._per_source_refs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    # ── add / remove ──────────────────────────────────────────────────────

    async def add(
        self,
        ws: WSConnId,
        symbols: Iterable[str],
    ) -> SubscribeDiff:
        """Subscribe ``ws`` to ``symbols``. Caps are evaluated per symbol in
        order ``rate_limit → per_ws → global``; first cap kind encountered in
        the batch wins ``rejected_reason``. Rate-limit is evaluated first so
        a flood (whether of accepts or rejects) is bailed out early — the
        rate window counts every attempt, not just accepts."""
        diff = SubscribeDiff()
        # Materialise to avoid late-evaluation surprises from generators.
        symbol_list = [CanonicalId(s) for s in symbols]
        if not symbol_list:
            return diff

        async with self._lock:
            now = time.monotonic()
            self._evict_rate_window(ws, now)

            # Read existing set lazily — only write back once if anything sticks.
            ws_set = self._per_ws.get(ws)
            ws_set_dirty = False
            ws_set_view: set[CanonicalId] = ws_set if ws_set is not None else set()

            rate_bucket = self._rate_buckets.get(ws)
            rate_bucket_view: deque[float] = rate_bucket if rate_bucket is not None else deque()
            is_internal_ws = isinstance(ws, str) and ws.startswith("__internal:")
            cap_per_ws = (
                self._cap_per_ws_override.get(ws, self._cap_per_ws)  # type: ignore[arg-type]
                if is_internal_ws
                else self._cap_per_ws
            )

            for sym in symbol_list:
                if sym in ws_set_view:
                    continue  # idempotent — already counted

                # Count every attempt against the rate window — flood
                # protection must include rejected attempts.
                if not is_internal_ws:
                    rate_bucket_view.append(now)

                if not is_internal_ws and len(rate_bucket_view) > self._rate_limit_per_minute:
                    diff.rejected.add(sym)
                    diff.rejected_rate_limit.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "rate_limit"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
                        cap_kind="rate_limit",
                        source="",
                        asset_class="",
                    ).inc()
                    continue

                if len(ws_set_view) >= cap_per_ws:
                    diff.rejected.add(sym)
                    diff.rejected_per_ws.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "per_ws"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
                        cap_kind="per_ws",
                        source="",
                        asset_class="",
                    ).inc()
                    continue

                if len(self._global_refs) >= self._cap_global and sym not in self._global_refs:
                    diff.rejected.add(sym)
                    diff.rejected_global.add(sym)
                    diff.rejected_reason = diff.rejected_reason or "global"
                    QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
                        cap_kind="global",
                        source="",
                        asset_class="",
                    ).inc()
                    continue

                # Phase 7c CRIT-1 layer 1: per-source soft cap (only when a
                # route has been assigned by SourceRouter; pre-route subs
                # bypass — they'll be capped on a later add() once routed).
                source = self._routes.get(sym)
                if source is not None:
                    source_str = str(source)
                    asset_class = sym.split(":", 1)[0] if ":" in sym else ""
                    if self._per_source_refs.get(source_str, 0) >= CAP_PER_SOURCE:
                        diff.rejected.add(sym)
                        diff.rejected_per_source.add(sym)
                        diff.rejected_reason = diff.rejected_reason or "per_source"
                        QUOTE_SUBSCRIPTION_CAP_REJECTED_TOTAL.labels(
                            cap_kind="per_source",
                            source=source_str,
                            asset_class=asset_class,
                        ).inc()
                        continue

                ws_set_view.add(sym)
                ws_set_dirty = True
                prev = self._global_refs.get(sym, 0)
                self._global_refs[sym] = prev + 1
                if prev == 0:
                    diff.added.add(sym)
                    # 0→1 transition — bump per-source refcount if routed.
                    if source is not None:
                        source_str = str(source)
                        if (
                            len(self._per_source_refs) < MAX_SOURCES
                            or source_str in self._per_source_refs
                        ):
                            self._per_source_refs[source_str] = (
                                self._per_source_refs.get(source_str, 0) + 1
                            )

            # Only persist the per-WS set + rate bucket if we actually
            # touched them — avoids phantom defaultdict entries leaking
            # for empty / fully-rejected batches.
            if ws_set_dirty and ws_set is None:
                self._per_ws[ws] = ws_set_view
            if rate_bucket is None and rate_bucket_view:
                self._rate_buckets[ws] = rate_bucket_view

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
                # MED fix: clear orphan rate bucket when the ws_set drains to
                # empty. Without this, a client that fully unsubscribes leaves
                # a non-expiring deque in _rate_buckets consuming memory.
                self._rate_buckets.pop(ws, None)
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

    async def decrement_for_source(self, source: str, count: int = 1) -> None:
        """Drop _per_source_refs[source] by ``count`` to recover ghost subs.

        Called by sidecar_stream when a sidecar reports a drift-detected
        upstream rejection (Phase 7c HIGH-6). Pops the entry on 0.
        """
        async with self._lock:
            cur = self._per_source_refs.get(source, 0)
            new_val = max(0, cur - count)
            if new_val == 0:
                self._per_source_refs.pop(source, None)
            else:
                self._per_source_refs[source] = new_val

    def _decrement_locked(
        self,
        sym: CanonicalId,
        diff: UnsubscribeDiff,
    ) -> None:
        prev = self._global_refs.get(sym, 0)
        if prev <= 1:
            # Phase 7c CRIT-1: pop the route BEFORE we delete it, so we know
            # which per-source counter to decrement.
            source = self._routes.pop(sym, None)
            self._global_refs.pop(sym, None)
            diff.removed.add(sym)
            if source is not None:
                source_str = str(source)
                cur = self._per_source_refs.get(source_str, 0)
                if cur <= 1:
                    self._per_source_refs.pop(source_str, None)
                else:
                    self._per_source_refs[source_str] = cur - 1
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
        """Set the upstream source for a canonical_id.

        Sync intentionally — SourceRouter (Task B3) is the only writer, and
        because Python sync code runs uninterrupted between asyncio await
        points, a sync write is atomic relative to any in-flight ``add`` /
        ``remove`` (which only ``await`` on lock acquisition). Multi-worker
        (Phase 24) will move route state to Redis and re-evaluate this
        contract.
        """
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
