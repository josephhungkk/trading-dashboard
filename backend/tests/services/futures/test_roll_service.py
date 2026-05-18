"""RollService tests — CRUD, nonce single-use, dedup, partial-fill path."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.futures.roll_service import RollService


@pytest.fixture
def redis_mock() -> AsyncMock:
    m = AsyncMock()
    m.get.return_value = None
    m.getdel.return_value = None
    m.exists.return_value = 0
    return m


@pytest.fixture
def roll_service(redis_mock: AsyncMock) -> RollService:
    return RollService(
        redis=redis_mock, config=AsyncMock(), orders_service=AsyncMock(), telegram=AsyncMock()
    )


@pytest.mark.asyncio
async def test_nonce_single_use(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """GETDEL returns payload on first call, None on second."""
    account_id = str(uuid.uuid4())
    nonce = "test-nonce-123"
    payload = {
        "instrument_id": 42,
        "close_conid": "111",
        "open_conid": "222",
        "account_id": account_id,
    }
    redis_mock.getdel.side_effect = [json.dumps(payload).encode(), None]

    first = await roll_service._consume_nonce(account_id, nonce)
    assert first is not None
    assert first["instrument_id"] == 42

    second = await roll_service._consume_nonce(account_id, nonce)
    assert second is None


@pytest.mark.asyncio
async def test_dedup_same_instrument(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """Pending nonce for ESM25 blocks re-notification for same instrument."""
    account_id = str(uuid.uuid4())
    instrument_id = 42
    redis_mock.exists.return_value = 1

    should_notify = await roll_service._should_notify(account_id, instrument_id)
    assert should_notify is False


@pytest.mark.asyncio
async def test_dedup_cross_instrument(roll_service: RollService, redis_mock: AsyncMock) -> None:
    """Pending ESM25 roll does NOT suppress NQM25 notification."""
    account_id = str(uuid.uuid4())
    esm25_instrument_id = 42
    nqm25_instrument_id = 99

    async def exists_side_effect(key: str) -> int:
        if str(esm25_instrument_id) in key:
            return 1
        return 0

    redis_mock.exists.side_effect = exists_side_effect

    assert await roll_service._should_notify(account_id, esm25_instrument_id) is False
    assert await roll_service._should_notify(account_id, nqm25_instrument_id) is True


@pytest.mark.asyncio
async def test_cross_account_nonce_rejected(
    roll_service: RollService, redis_mock: AsyncMock
) -> None:
    """Nonce with mismatched account_id in payload → 404 equivalent."""
    real_account = str(uuid.uuid4())
    evil_account = str(uuid.uuid4())
    nonce = "nonce-xyz"
    payload = {
        "instrument_id": 42,
        "close_conid": "111",
        "open_conid": "222",
        "account_id": evil_account,
    }
    redis_mock.getdel.return_value = json.dumps(payload).encode()

    result = await roll_service._consume_nonce(real_account, nonce)
    assert result is None
