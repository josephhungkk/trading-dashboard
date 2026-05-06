"""Unit tests for OcoOrchestrator Redis advisory lock behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_lock_acquisition_makes_leader() -> None:
    redis = AsyncMock()
    redis.set.return_value = True  # nx=True succeeds → we are leader
    db = _make_db_factory()
    orch = OcoOrchestrator(db=db, redis=redis)
    await orch.start()
    assert orch._leader is True
    assert orch._renewal_task is not None
    await orch.stop()


@pytest.mark.asyncio
async def test_lock_taken_makes_follower() -> None:
    redis = AsyncMock()
    redis.set.return_value = None  # nx=True fails → another leader holds lock
    db = _make_db_factory()
    orch = OcoOrchestrator(db=db, redis=redis)
    await orch.start()
    assert orch._leader is False
    assert orch._renewal_task is None
    await orch.stop()
