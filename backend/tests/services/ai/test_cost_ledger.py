from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import metrics
from app.services.ai.cost_ledger import (
    _BATCH_SIZE,
    _QUEUE_MAX,
    CompletionRecord,
    CostLedger,
)

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def reset_cost_ledger_metrics() -> None:
    metrics.AI_COST_LEDGER_DROPS_TOTAL._value.set(0)
    metrics.AI_COST_LEDGER_INSERT_FAILURES_TOTAL._value.set(0)


def _session_factory() -> tuple[MagicMock, AsyncMock]:
    session_factory = MagicMock()
    session = AsyncMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return session_factory, session


def _record(request_id: str = "req-1") -> CompletionRecord:
    return CompletionRecord(
        request_id=request_id,
        ts=datetime.now(UTC),
        provider="openai",
        model="gpt-test",
        capability="chat",
        prompt_tokens=10,
        completion_tokens=20,
        wall_time_ms=123.4,
        outcome="success",
        host="nuc",
    )


async def _wait_for_execute(session: AsyncMock, *, timeout_s: float = 2.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while session.execute.await_count == 0:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("session.execute was not awaited")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_single_record_flushes_after_interval() -> None:
    session_factory, session = _session_factory()
    ledger = CostLedger(session_factory)

    await ledger.start()
    ledger.record(_record())
    await _wait_for_execute(session)
    await ledger.stop()

    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_batch_flushes_immediately() -> None:
    session_factory, session = _session_factory()
    ledger = CostLedger(session_factory)

    await ledger.start()
    for index in range(_BATCH_SIZE):
        ledger.record(_record(f"req-{index}"))
    await _wait_for_execute(session, timeout_s=1.0)
    await ledger.stop()

    session.execute.assert_awaited_once()
    assert len(session.execute.await_args.args[1]) == _BATCH_SIZE


@pytest.mark.asyncio
async def test_overflow_drops_oldest_and_increments_counter() -> None:
    session_factory, _session = _session_factory()
    ledger = CostLedger(session_factory)

    records = [_record(f"req-{index}") for index in range(_QUEUE_MAX + 1)]
    for rec in records:
        ledger.record(rec)

    assert metrics.AI_COST_LEDGER_DROPS_TOTAL._value.get() == 1
    queued_ids = [ledger._queue.get_nowait().request_id for _ in range(_QUEUE_MAX)]
    assert "req-0" not in queued_ids
    assert queued_ids[0] == "req-1"
    assert queued_ids[-1] == f"req-{_QUEUE_MAX}"

    await ledger.stop()


@pytest.mark.asyncio
async def test_pg_failure_increments_insert_failures_and_continues() -> None:
    session_factory, _session = _session_factory()
    session_factory.return_value.__aenter__.side_effect = Exception("pg down")
    ledger = CostLedger(session_factory)

    await ledger.start()
    for index in range(_BATCH_SIZE):
        ledger.record(_record(f"req-{index}"))
    deadline = asyncio.get_running_loop().time() + 1.0
    while metrics.AI_COST_LEDGER_INSERT_FAILURES_TOTAL._value.get() != _BATCH_SIZE:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("insert failure metric did not increment")
        await asyncio.sleep(0.01)

    assert ledger._task is not None
    assert not ledger._task.done()

    await ledger.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_pending() -> None:
    session_factory, session = _session_factory()
    ledger = CostLedger(session_factory)

    await ledger.start()
    for index in range(3):
        ledger.record(_record(f"req-{index}"))
    await ledger.stop()

    session.execute.assert_awaited_once()
    assert len(session.execute.await_args.args[1]) == 3


@pytest.mark.asyncio
async def test_record_after_stop_is_noop() -> None:
    session_factory, session = _session_factory()
    ledger = CostLedger(session_factory)

    await ledger.start()
    await ledger.stop()
    ledger.record(_record())

    assert ledger._queue.empty()
    session.execute.assert_not_awaited()
