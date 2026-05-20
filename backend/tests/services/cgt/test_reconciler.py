from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cgt.importers.reconciler import reconcile


def _make_session(rows: list) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_reconcile_no_orphans():
    session = _make_session([])
    result = await reconcile(uuid.uuid4(), session)
    assert result == {"orphaned_live_fills": 0}


@pytest.mark.asyncio
async def test_reconcile_reports_orphans():
    rows = [MagicMock(), MagicMock()]
    session = _make_session(rows)
    result = await reconcile(uuid.uuid4(), session)
    assert result == {"orphaned_live_fills": 2}
