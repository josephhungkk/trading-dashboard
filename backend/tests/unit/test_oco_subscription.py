"""Unit tests for OcoOrchestrator stream management (cap + idle eviction)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.oco_orchestrator import (
    IDLE_STREAM_SECONDS,
    MAX_STREAMS,
    CapacityError,
    OcoOrchestrator,
)


def _make_db_factory() -> MagicMock:
    """Return a sync-callable factory that yields an async context-manager session."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = []
    session.execute.return_value = mock_result
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


async def _leader_orch() -> OcoOrchestrator:
    redis = AsyncMock()
    redis.set.return_value = True
    orch = OcoOrchestrator(db=_make_db_factory(), redis=redis)
    await orch.start()
    return orch


async def _noop(broker_id: str, account_id: str) -> None:
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_stream_opened_and_reused() -> None:
    orch = await _leader_orch()
    orch._stream_order_events = _noop  # type: ignore[method-assign]
    await orch._ensure_stream("futu", "acct1")
    first_task = orch._streams[("futu", "acct1")]
    await orch._ensure_stream("futu", "acct1")  # reuse — same task
    assert orch._streams[("futu", "acct1")] is first_task
    await orch.stop()


@pytest.mark.asyncio
async def test_capacity_error_at_limit() -> None:
    orch = await _leader_orch()

    async def _long_noop(broker_id: str, account_id: str) -> None:
        await asyncio.sleep(60)

    orch._stream_order_events = _long_noop  # type: ignore[method-assign]
    for i in range(MAX_STREAMS):
        await orch._ensure_stream("futu", f"acct{i}")
    with pytest.raises(CapacityError, match="capacity_exhausted"):
        await orch._ensure_stream("futu", "overflow")
    await orch.stop()


@pytest.mark.asyncio
async def test_idle_stream_eviction() -> None:
    orch = await _leader_orch()
    orch._stream_order_events = _noop  # type: ignore[method-assign]

    # Fake clock: start at 0, then advance past idle threshold
    _t = [0.0]

    def _fake_clock() -> float:
        return _t[0]

    orch._clock = _fake_clock
    await orch._ensure_stream("futu", "acct1")
    _t[0] = IDLE_STREAM_SECONDS + 1.0  # advance past idle threshold
    await orch._close_idle_streams()
    assert ("futu", "acct1") not in orch._streams
    await orch.stop()
