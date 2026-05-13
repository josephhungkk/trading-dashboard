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
from dataclasses import dataclass


@dataclass(slots=True)
class EvaluatorMetrics:
    queue_dropped_total: int = 0
    debounced_total: int = 0
    debounce_evicted_total: int = 0
    snapshot_rebuilds_total: int = 0
    snapshot_rebuild_coalesced_total: int = 0
    eval_errors_total: int = 0


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
        queue_maxsize: int = 1000,
        debounce_seconds: float = 0.5,
        snapshot_coalesce_seconds: float = 0.25,
        debounce_sweep_seconds: float = 60.0,
        _rebuild_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=queue_maxsize)
        self._debounce_seconds = debounce_seconds
        self._snapshot_coalesce_seconds = snapshot_coalesce_seconds
        self._debounce_sweep_seconds = debounce_sweep_seconds
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

    def _producer_debounce_check(self, *, rule_id: int, symbol: str, now: float) -> bool:
        key = (rule_id, symbol)
        last = self._debounce_last_at.get(key)
        if last is not None and (now - last) < self._debounce_seconds:
            self._metrics.debounced_total += 1
            return False
        self._debounce_last_at[key] = now
        return True

    def _sweep_debounce(self, *, now: float, max_age_seconds: float = 60.0) -> None:
        stale = [k for k, ts in self._debounce_last_at.items() if (now - ts) > max_age_seconds]
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
        chunk-fill insert burst can't starve the worker.
        """
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return
        inst_id = obj.get("inst_id")
        ts = obj.get("ts")
        if inst_id is None:
            return
        symbol = resolve_symbol(int(inst_id))
        if symbol is None:
            return
        now = time.monotonic()
        for rule_id in self._index.rules_for(symbol):
            if self._producer_debounce_check(rule_id=rule_id, symbol=symbol, now=now):
                await self._enqueue({"rule_id": rule_id, "symbol": symbol, "ts": ts})

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._debounce_sweep_seconds)
            self._sweep_debounce(now=time.monotonic())

    async def start(self) -> None:
        self._sweep_task = asyncio.create_task(self._sweep_loop())

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
