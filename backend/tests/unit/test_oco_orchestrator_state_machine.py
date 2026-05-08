"""Unit tests for OcoOrchestrator 9-state machine transitions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.oco_orchestrator import OcoOrchestrator


def _make_db_factory(rows: list | None = None) -> MagicMock:
    """Return a sync-callable factory that yields an async context-manager session."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows or []
    session.execute.return_value = mock_result
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory


def _make_link(status: str = "PENDING_BOTH") -> dict:
    return {
        "id": uuid4(),
        "broker_id": "futu",
        "account_id": uuid4(),
        "order_id_a": "A1",
        "order_id_b": "B1",
        "status": status,
        "filled_leg_id": None,
        "failure_reason": None,
    }


async def _leader_orch() -> OcoOrchestrator:
    redis = AsyncMock()
    redis.set.return_value = True
    db = _make_db_factory()
    orch = OcoOrchestrator(db=db, redis=redis)
    await orch.start()
    return orch


@pytest.mark.asyncio
async def test_leg_a_fill_triggers_cancel_b() -> None:
    orch = await _leader_orch()
    link = _make_link()
    orch._active[str(link["id"])] = link
    orch._cancel = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await orch.process_fill_event("futu", "A1", {"qty": "100"})
    assert orch._cancel.await_count == 1
    assert link["status"] == "COMPLETED"
    await orch.stop()


@pytest.mark.asyncio
async def test_leg_b_fill_triggers_cancel_a() -> None:
    orch = await _leader_orch()
    link = _make_link()
    orch._active[str(link["id"])] = link
    orch._cancel = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await orch.process_fill_event("futu", "B1", {"qty": "100"})
    assert orch._cancel.await_count == 1
    assert link["status"] == "COMPLETED"
    await orch.stop()


@pytest.mark.asyncio
async def test_cancel_failure_sets_cancel_failed() -> None:
    orch = await _leader_orch()
    link = _make_link()
    orch._active[str(link["id"])] = link
    orch._cancel = AsyncMock(return_value=False)  # type: ignore[method-assign]
    await orch.process_fill_event("futu", "A1", {"qty": "100"})
    assert link["status"] == "CANCEL_FAILED"
    assert "cancel_rejected" in (link["failure_reason"] or "")
    await orch.stop()


@pytest.mark.asyncio
async def test_terminal_status_blocks_transition() -> None:
    orch = await _leader_orch()
    link = _make_link(status="COMPLETED")
    with pytest.raises(ValueError, match="terminal"):
        await orch._transition(link, "PENDING_BOTH")
    await orch.stop()


@pytest.mark.asyncio
async def test_follower_ignores_fill_event() -> None:
    redis = AsyncMock()
    redis.set.return_value = None  # follower
    db = _make_db_factory()
    orch = OcoOrchestrator(db=db, redis=redis)
    await orch.start()
    # No link added; follower must return silently — db factory must not be called
    db.reset_mock()
    await orch.process_fill_event("futu", "A1", {})
    db.assert_not_called()
    await orch.stop()


@pytest.mark.asyncio
async def test_double_fill_race_serialized_by_per_link_lock() -> None:
    """Two concurrent fill events for the same OCO link must not both proceed
    through the cancel path. The per-link asyncio.Lock (CRIT-code-2 / HIGH-code-3)
    and in-lock re-check of terminal status must cause the second coroutine to
    exit early after seeing the link already in a terminal state.
    """
    orch = await _leader_orch()
    link = _make_link()
    orch._active[str(link["id"])] = link

    cancel_call_count = 0

    async def _slow_cancel(broker_id: str, account_id: str, order_id: str) -> bool:
        nonlocal cancel_call_count
        cancel_call_count += 1
        await asyncio.sleep(0)  # yield so the second coroutine gets a chance to run
        return True

    orch._cancel = _slow_cancel  # type: ignore[method-assign]

    # Fire two concurrent fill events for the same leg — only the first should win.
    await asyncio.gather(
        orch.process_fill_event("futu", "A1", {"qty": "100"}),
        orch.process_fill_event("futu", "A1", {"qty": "100"}),
    )

    # Cancel must be called exactly once — the second fill sees terminal status
    # inside the lock and exits without calling _cancel again.
    assert cancel_call_count == 1, (
        f"expected 1 cancel call (second fill should be dropped), got {cancel_call_count}"
    )
    assert link["status"] == "COMPLETED"
    await orch.stop()
