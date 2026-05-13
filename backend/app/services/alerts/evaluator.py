"""Phase 11b chunk B1: in-process FastAPI lifespan alerts evaluator skeleton.

Producer-side debounce (per-`(rule_id, symbol)`, 500ms default) drops repeat
events BEFORE the bounded queue. Worker dequeues, dispatches to predicates,
fires deliveries (chunk C wires these in).

Inverted index `symbol -> {rule_id}` is rebuilt on Redis pubsub
`app_config:invalidate:alerts` with a 250ms coalescing window — multiple
invalidations within the window collapse to ONE rebuild. Matches Phase 10b.2
portfolio WS "compute cache + debounce" pattern.

Per-rule fail-isolation lives at the worker boundary (chunk B2). Wired into
`app/main.py` lifespan alongside `orphan_sweeper` and `BalanceSnapshotWriter`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# Spec §6 producer-side defaults. Centralized so a spec revision touches
# one place rather than scattered literals.
_DEFAULT_QUEUE_MAXSIZE = 1000
_DEFAULT_DEBOUNCE_SECONDS = 0.5
_DEFAULT_SNAPSHOT_COALESCE_SECONDS = 0.25
_DEFAULT_DEBOUNCE_SWEEP_SECONDS = 60.0
_DEFAULT_DEBOUNCE_MAX_AGE_SECONDS = 60.0

# spec §6 source labels for `alerts_evaluator_debounced_total{source}`
_SOURCE_LISTEN = "listen"
_SOURCE_TICKS = "ticks"


@dataclass(slots=True)
class EvaluatorMetrics:
    queue_dropped_total: int = 0
    # `alerts_evaluator_debounced_total{source}` — spec §6 keys by producer.
    debounced_total_by_source: dict[str, int] = field(default_factory=dict)
    debounce_evicted_total: int = 0
    snapshot_rebuilds_total: int = 0
    snapshot_rebuild_coalesced_total: int = 0
    eval_errors_total: int = 0

    @property
    def debounced_total(self) -> int:
        """Sum across all source labels — kept for backward-compat with
        callers that don't care about the breakdown."""
        return sum(self.debounced_total_by_source.values())


class InvertedIndex:
    """In-memory symbol -> {rule_id} index. add() upserts; remove() drops empties."""

    def __init__(self) -> None:
        self._symbol_to_rules: dict[str, set[int]] = {}
        self._rule_to_symbols: dict[int, set[str]] = {}

    def add(self, *, rule_id: int, symbols: set[str]) -> None:
        self.remove(rule_id=rule_id)
        self._rule_to_symbols[rule_id] = set(symbols)
        for s in symbols:
            self._symbol_to_rules.setdefault(s, set()).add(rule_id)

    def remove(self, *, rule_id: int) -> None:
        symbols = self._rule_to_symbols.pop(rule_id, set())
        for s in symbols:
            bucket = self._symbol_to_rules.get(s)
            if bucket is not None:
                bucket.discard(rule_id)
                if not bucket:
                    del self._symbol_to_rules[s]

    def rules_for(self, symbol: str) -> set[int]:
        return self._symbol_to_rules.get(symbol, set()).copy()


class AlertsEvaluator:
    def __init__(
        self,
        *,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        snapshot_coalesce_seconds: float = _DEFAULT_SNAPSHOT_COALESCE_SECONDS,
        debounce_sweep_seconds: float = _DEFAULT_DEBOUNCE_SWEEP_SECONDS,
        debounce_max_age_seconds: float = _DEFAULT_DEBOUNCE_MAX_AGE_SECONDS,
        max_age_fn: Callable[[int, str], float] | None = None,
        _rebuild_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=queue_maxsize)
        self._debounce_seconds = debounce_seconds
        self._snapshot_coalesce_seconds = snapshot_coalesce_seconds
        self._debounce_sweep_seconds = debounce_sweep_seconds
        # Spec §6: evict `(rule_id, symbol)` older than
        # ``max(window_seconds * 10, 60s)``. Producers wire ``max_age_fn``
        # to look up the rule's window; default uses the configured floor.
        self._debounce_max_age_seconds = debounce_max_age_seconds
        self._max_age_fn = max_age_fn
        self._debounce_last_at: dict[tuple[int, str], float] = {}
        self._index = InvertedIndex()
        self._metrics = EvaluatorMetrics()
        self._rebuild_pending = False
        self._rebuild_lock = asyncio.Lock()
        self._rebuild_task: asyncio.Task[None] | None = None
        self._sweep_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._rebuild_fn = _rebuild_fn or self._noop_rebuild

    @property
    def metrics(self) -> EvaluatorMetrics:
        return self._metrics

    @property
    def index(self) -> InvertedIndex:
        return self._index

    async def _noop_rebuild(self) -> None:
        pass

    def _producer_debounce_check(
        self,
        *,
        rule_id: int,
        symbol: str,
        now: float,
        source: str = _SOURCE_LISTEN,
    ) -> bool:
        """Return True if the event should be enqueued; False to drop.

        ``source`` labels the producer ('listen' | 'ticks') so the
        ``alerts_evaluator_debounced_total{source}`` Prometheus counter can
        distinguish bars_1m NOTIFY drops from internal-bus tick drops.
        """
        key = (rule_id, symbol)
        last = self._debounce_last_at.get(key)
        if last is not None and (now - last) < self._debounce_seconds:
            self._metrics.debounced_total_by_source[source] = (
                self._metrics.debounced_total_by_source.get(source, 0) + 1
            )
            return False
        self._debounce_last_at[key] = now
        return True

    def _sweep_debounce(self, *, now: float, max_age_seconds: float | None = None) -> None:
        """Evict stale (rule_id, symbol) debounce entries.

        Per spec §6, eviction age = ``max(window_seconds * 10, 60s)`` per rule.
        If ``self._max_age_fn`` is configured, it's consulted per-key; otherwise
        every key uses ``max_age_seconds`` (default ``_debounce_max_age_seconds``).
        Test callers pass ``max_age_seconds`` for deterministic eviction.
        """
        floor = max_age_seconds if max_age_seconds is not None else self._debounce_max_age_seconds
        stale: list[tuple[int, str]] = []
        for key, ts in self._debounce_last_at.items():
            rule_id, symbol = key
            per_key_max = floor
            if self._max_age_fn is not None and max_age_seconds is None:
                per_key_max = max(self._max_age_fn(rule_id, symbol), floor)
            if (now - ts) > per_key_max:
                stale.append(key)
        for k in stale:
            del self._debounce_last_at[k]
        self._metrics.debounce_evicted_total += len(stale)

    async def _enqueue(self, item: dict[str, object]) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(item)
            self._metrics.queue_dropped_total += 1

    def request_snapshot_rebuild(self) -> None:
        self._rebuild_pending = True
        if self._rebuild_task is None or self._rebuild_task.done():
            self._rebuild_task = asyncio.create_task(self._coalesced_rebuild())

    async def _coalesced_rebuild(self) -> None:
        await asyncio.sleep(self._snapshot_coalesce_seconds)
        async with self._rebuild_lock:
            coalesced = -1  # we always do at least 1 actual rebuild; -1 cancels it out
            while self._rebuild_pending:
                self._rebuild_pending = False
                coalesced += 1
                await self._rebuild_fn()
            self._metrics.snapshot_rebuilds_total += 1
            if coalesced > 0:
                self._metrics.snapshot_rebuild_coalesced_total += coalesced

    async def _on_bars_1m_notify(
        self,
        payload: str,
        *,
        resolve_symbol: Callable[[int], str | None],
    ) -> None:
        """Decode a `pg_notify('bars_1m_insert', ...)` payload and enqueue
        one evaluation request per rule indexed for the resolved symbol.

        Producer-side debounce gates each `(rule_id, symbol)` pair so a
        chunk-fill insert burst can't starve the worker. Malformed payloads
        (non-JSON, missing inst_id, non-numeric inst_id) are dropped silently
        — the LISTEN bus must survive a bad row.
        """
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return
        raw_inst_id = obj.get("inst_id") if isinstance(obj, dict) else None
        ts = obj.get("ts") if isinstance(obj, dict) else None
        if raw_inst_id is None:
            return
        try:
            inst_id = int(raw_inst_id)
        except TypeError, ValueError:
            return
        symbol = resolve_symbol(inst_id)
        if symbol is None:
            return
        now = time.monotonic()
        for rule_id in self._index.rules_for(symbol):
            if self._producer_debounce_check(
                rule_id=rule_id, symbol=symbol, now=now, source=_SOURCE_LISTEN
            ):
                await self._enqueue({"rule_id": rule_id, "symbol": symbol, "ts": ts})

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._debounce_sweep_seconds)
            self._sweep_debounce(now=time.monotonic())

    async def _worker_loop(
        self,
        *,
        process: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        """Dequeue and dispatch evaluation requests.

        ``process`` is the lifespan-injected callback that owns:
        rule load + state population + predicate evaluate + alert_fires
        write + delivery dispatch + per-rule fail-isolation. Keeping it
        injectable lets the evaluator stay unaware of SQLAlchemy/Redis
        wiring details and keeps the test surface narrow.

        Per-item exceptions are swallowed (logged via metrics counter).
        One bad event must NEVER abort the worker — spec §6 fail-isolation
        boundary.
        """
        while True:
            item = await self._queue.get()
            try:
                await process(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._metrics.eval_errors_total += 1

    async def start(self) -> None:
        self._sweep_task = asyncio.create_task(self._sweep_loop())

    def start_worker(
        self,
        *,
        process: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        """Launch the worker task. Separate from ``start()`` so tests that
        only exercise the producer/index don't need to supply a process
        callback (and so the lifespan controller can construct the
        process closure after the dispatcher + state-loader exist)."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(process=process))

    async def stop(self) -> None:
        for task in (self._sweep_task, self._rebuild_task, self._worker_task):
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
