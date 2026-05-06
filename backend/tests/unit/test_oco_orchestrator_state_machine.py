"""Unit tests for OcoOrchestrator 9-state machine transitions."""

from __future__ import annotations

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
