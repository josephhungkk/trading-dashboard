"""Phase 11b chunk-B-close: AlertsEvaluator.start_worker + worker loop tests."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.no_db

from app.services.alerts.evaluator import AlertsEvaluator  # noqa: E402


@pytest.mark.asyncio
async def test_worker_consumes_queue_and_calls_process() -> None:
    """The worker dequeues items and dispatches them to ``process``."""
    evaluator = AlertsEvaluator(queue_maxsize=100)
    seen: list[dict[str, object]] = []

    async def process(item: dict[str, object]) -> None:
        seen.append(item)

    evaluator.start_worker(process=process)
    try:
        await evaluator._queue.put({"rule_id": 1, "symbol": "AAPL"})
        await evaluator._queue.put({"rule_id": 2, "symbol": "TSLA"})
        # Give the worker a moment to pick both items up.
        for _ in range(20):
            if len(seen) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await evaluator.stop()

    assert {tuple(sorted(s.items())) for s in seen} == {
        (("rule_id", 1), ("symbol", "AAPL")),
        (("rule_id", 2), ("symbol", "TSLA")),
    }


@pytest.mark.asyncio
async def test_worker_swallows_process_exceptions() -> None:
    """One bad event must not abort the worker (spec §6 fail-isolation)."""
    evaluator = AlertsEvaluator(queue_maxsize=100)
    seen: list[int] = []

    async def process(item: dict[str, object]) -> None:
        rid = item.get("rule_id")
        assert isinstance(rid, int)
        if rid == 99:
            raise RuntimeError("boom")
        seen.append(rid)

    evaluator.start_worker(process=process)
    try:
        await evaluator._queue.put({"rule_id": 99, "symbol": "X"})
        await evaluator._queue.put({"rule_id": 1, "symbol": "Y"})
        for _ in range(30):
            if seen:
                break
            await asyncio.sleep(0.01)
    finally:
        await evaluator.stop()

    assert seen == [1]
    # eval_errors_total tracks the swallowed exception.
    assert evaluator.metrics.eval_errors_total >= 1


@pytest.mark.asyncio
async def test_start_worker_is_idempotent() -> None:
    """Calling start_worker twice must NOT spawn two workers (test guard)."""
    evaluator = AlertsEvaluator(queue_maxsize=100)

    async def process(_item: dict[str, object]) -> None:
        return

    evaluator.start_worker(process=process)
    first_task = evaluator._worker_task
    evaluator.start_worker(process=process)
    second_task = evaluator._worker_task
    try:
        assert first_task is second_task
    finally:
        await evaluator.stop()
