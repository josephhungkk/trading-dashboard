"""Phase 11b chunk B1: evaluator skeleton tests — inverted index, producer
debounce, bounded queue drop-oldest, debounce-sweep eviction, snapshot
rebuild coalescing.
"""

from __future__ import annotations

import asyncio

from app.services.alerts.evaluator import AlertsEvaluator, InvertedIndex


def test_inverted_index_groups_by_symbol() -> None:
    idx = InvertedIndex()
    idx.add(rule_id=1, symbols={"AAPL", "TSLA"})
    idx.add(rule_id=2, symbols={"AAPL"})
    assert idx.rules_for("AAPL") == {1, 2}
    assert idx.rules_for("TSLA") == {1}
    assert idx.rules_for("MSFT") == set()


def test_inverted_index_remove_drops_empty_symbols() -> None:
    idx = InvertedIndex()
    idx.add(rule_id=1, symbols={"AAPL"})
    idx.remove(rule_id=1)
    assert idx.rules_for("AAPL") == set()


async def test_producer_debounce_drops_within_500ms() -> None:
    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.5)
    accepted_1 = evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.0)
    accepted_2 = evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.3)
    accepted_3 = evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.6)
    assert accepted_1 is True
    assert accepted_2 is False
    assert accepted_3 is True
    # debounced_total aggregates across sources (default 'listen').
    assert evaluator.metrics.debounced_total == 1


async def test_producer_debounce_records_source_dimension() -> None:
    """Spec §6: alerts_evaluator_debounced_total{source} keys by producer."""
    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.5)
    # Two LISTEN drops, one TICKS drop.
    evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.0, source="listen")
    evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.1, source="listen")
    evaluator._producer_debounce_check(rule_id=1, symbol="AAPL", now=1000.2, source="listen")
    evaluator._producer_debounce_check(rule_id=2, symbol="TSLA", now=2000.0, source="ticks")
    evaluator._producer_debounce_check(rule_id=2, symbol="TSLA", now=2000.1, source="ticks")
    by_source = evaluator.metrics.debounced_total_by_source
    assert by_source["listen"] == 2  # first call seeds, 2nd+3rd are drops
    assert by_source["ticks"] == 1
    assert evaluator.metrics.debounced_total == 3  # aggregate property


async def test_queue_drop_oldest_on_overflow() -> None:
    evaluator = AlertsEvaluator(queue_maxsize=2, debounce_seconds=0.0)
    await evaluator._enqueue({"rule_id": 1, "symbol": "A"})
    await evaluator._enqueue({"rule_id": 2, "symbol": "B"})
    await evaluator._enqueue({"rule_id": 3, "symbol": "C"})  # forces drop-oldest
    items: list[dict[str, object]] = []
    while not evaluator._queue.empty():
        items.append(evaluator._queue.get_nowait())
    assert len(items) == 2
    assert items[-1]["symbol"] == "C"
    assert evaluator.metrics.queue_dropped_total == 1


def test_debounce_sweep_evicts_stale() -> None:
    evaluator = AlertsEvaluator(queue_maxsize=10, debounce_seconds=0.5)
    evaluator._producer_debounce_check(rule_id=1, symbol="A", now=0.0)
    evaluator._producer_debounce_check(rule_id=2, symbol="B", now=1050.0)
    evaluator._sweep_debounce(now=1100.0, max_age_seconds=60.0)
    # rule 1 entry was at t=0; now=1100; age=1100 > 60 -> evict.
    # rule 2 entry was at t=1050; now=1100; age=50 < 60 -> keep.
    assert (1, "A") not in evaluator._debounce_last_at
    assert (2, "B") in evaluator._debounce_last_at
    assert evaluator.metrics.debounce_evicted_total == 1


async def test_snapshot_rebuild_coalescing() -> None:
    rebuild_calls: list[float] = []

    async def fake_rebuild() -> None:
        rebuild_calls.append(asyncio.get_event_loop().time())

    evaluator = AlertsEvaluator(
        queue_maxsize=10,
        debounce_seconds=0.5,
        snapshot_coalesce_seconds=0.05,
        _rebuild_fn=fake_rebuild,
    )
    await evaluator.start()
    try:
        for _ in range(10):
            evaluator.request_snapshot_rebuild()
        await asyncio.sleep(0.2)
        assert len(rebuild_calls) == 1
    finally:
        await evaluator.stop()


# ── B2: bars_1m LISTEN producer ────────────────────────────────────────


async def test_on_bars_1m_notify_enqueues_for_indexed_symbol() -> None:
    import json

    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})
    payload = json.dumps({"inst_id": 12345, "ts": 1700000000.0})

    def resolve(inst_id: int) -> str | None:
        return "AAPL" if inst_id == 12345 else None

    await evaluator._on_bars_1m_notify(payload, resolve_symbol=resolve)
    item = evaluator._queue.get_nowait()
    assert item["symbol"] == "AAPL"
    assert item["rule_id"] == 1
    assert item["ts"] == 1700000000.0


async def test_on_bars_1m_notify_drops_unresolvable_inst_id() -> None:
    import json

    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})
    payload = json.dumps({"inst_id": 99999, "ts": 1700000000.0})
    await evaluator._on_bars_1m_notify(payload, resolve_symbol=lambda _id: None)
    assert evaluator._queue.empty()


async def test_on_bars_1m_notify_no_rules_for_symbol() -> None:
    import json

    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    # Index has no rule for AAPL; payload still resolves.
    payload = json.dumps({"inst_id": 12345, "ts": 1700000000.0})
    await evaluator._on_bars_1m_notify(payload, resolve_symbol=lambda _id: "AAPL")
    assert evaluator._queue.empty()


async def test_on_bars_1m_notify_handles_malformed_payload() -> None:
    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})
    await evaluator._on_bars_1m_notify("not-json", resolve_symbol=lambda _id: "AAPL")
    assert evaluator._queue.empty()


# ── B-close reviewer fixes ─────────────────────────────────────────────


async def test_on_bars_1m_notify_drops_non_numeric_inst_id() -> None:
    """If a malformed row produces a non-numeric inst_id, drop silently
    instead of raising ValueError into the LISTEN callback."""
    import json

    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})
    payload = json.dumps({"inst_id": "not-a-number", "ts": 1700000000.0})
    await evaluator._on_bars_1m_notify(payload, resolve_symbol=lambda _id: "AAPL")
    assert evaluator._queue.empty()


async def test_on_bars_1m_notify_drops_non_dict_payload() -> None:
    """`json.loads` on a bare number/string succeeds but yields a non-dict —
    must not crash on `.get(...)`."""
    evaluator = AlertsEvaluator(queue_maxsize=100, debounce_seconds=0.0)
    evaluator.index.add(rule_id=1, symbols={"AAPL"})
    await evaluator._on_bars_1m_notify("42", resolve_symbol=lambda _id: "AAPL")
    assert evaluator._queue.empty()


def test_debounce_sweep_uses_max_age_fn_per_key() -> None:
    """Spec §6: eviction age per-key is max(window_seconds*10, 60s). When
    a max_age_fn is configured, long-window rules survive longer."""

    def max_age(rule_id: int, symbol: str) -> float:
        # Rule 1 has a 1-hour window → max_age = 36000s (long).
        # Rule 2 has a 1-minute window → max_age = 600s.
        return 36_000.0 if rule_id == 1 else 600.0

    evaluator = AlertsEvaluator(
        queue_maxsize=10,
        debounce_seconds=0.5,
        max_age_fn=max_age,
    )
    evaluator._producer_debounce_check(rule_id=1, symbol="A", now=0.0)
    evaluator._producer_debounce_check(rule_id=2, symbol="B", now=0.0)
    # At now=700, rule 2's 600s window has elapsed (gone), rule 1's hasn't.
    evaluator._sweep_debounce(now=700.0)
    assert (1, "A") in evaluator._debounce_last_at
    assert (2, "B") not in evaluator._debounce_last_at
