"""Integration test: HIGH-code-2 — SCHWAB_REFRESH_TOKEN_AGE_HOURS + USES_PER_24H gauges populated.

Verifies that _update_schwab_token_metrics reads issued_at from DB and
uses_count from Redis, then sets the Prometheus gauges correctly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_update_schwab_token_metrics_sets_age_gauge() -> None:
    """Must set SCHWAB_REFRESH_TOKEN_AGE_HOURS from DB issued_at and USES from Redis."""
    from app.core.metrics import SCHWAB_REFRESH_TOKEN_AGE_HOURS, SCHWAB_REFRESH_TOKEN_USES_PER_24H
    from app.main import _update_schwab_token_metrics

    issued = datetime.now(UTC) - timedelta(hours=10)
    issued_json = json.dumps(issued.isoformat())

    mock_row = MagicMock()
    mock_row.scalar_one_or_none.return_value = issued_json
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_row)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory = MagicMock(return_value=mock_session)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"5")

    age_set: list[float] = []
    uses_set: list[float] = []

    with (
        patch.object(
            SCHWAB_REFRESH_TOKEN_AGE_HOURS, "set", side_effect=lambda v: age_set.append(v)
        ),
        patch.object(
            SCHWAB_REFRESH_TOKEN_USES_PER_24H, "set", side_effect=lambda v: uses_set.append(v)
        ),
        patch("asyncio.sleep", side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _update_schwab_token_metrics(mock_redis, mock_db_factory)

    assert len(age_set) == 1
    assert 9.9 < age_set[0] < 10.1  # ~10 hours

    assert len(uses_set) == 1
    assert uses_set[0] == 5.0


@pytest.mark.asyncio
async def test_update_schwab_token_metrics_handles_missing_data() -> None:
    """Must not crash when DB row is absent; uses_count must default to 0."""
    from app.core.metrics import SCHWAB_REFRESH_TOKEN_USES_PER_24H
    from app.main import _update_schwab_token_metrics

    mock_row = MagicMock()
    mock_row.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_row)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory = MagicMock(return_value=mock_session)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    uses_set: list[float] = []

    with (
        patch.object(
            SCHWAB_REFRESH_TOKEN_USES_PER_24H, "set", side_effect=lambda v: uses_set.append(v)
        ),
        patch("asyncio.sleep", side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _update_schwab_token_metrics(mock_redis, mock_db_factory)

    assert uses_set == [0.0]
