"""Phase 8a - OrderCapabilityService cache + invalidation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.order_capability_service import OrderCapabilityService


def _result(row: dict[str, object] | None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


@pytest.mark.asyncio
async def test_is_supported_hits_db_then_caches() -> None:
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    assert await svc.is_supported("schwab", "MARKET", "DAY") is True
    assert await svc.is_supported("schwab", "MARKET", "DAY") is True
    assert db.execute.await_count == 1, "second call should hit cache, not DB"


@pytest.mark.asyncio
async def test_unknown_combo_returns_false_and_caches_negative() -> None:
    db = AsyncMock()
    db.execute.return_value = _result(None)
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    assert await svc.is_supported("schwab", "TRAIL", "DAY") is False
    assert await svc.is_supported("schwab", "TRAIL", "DAY") is False
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_cache_ttl_60s_expires() -> None:
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    redis = MagicMock()
    now = MagicMock(side_effect=[0.0, 61.0, 61.0])
    svc = OrderCapabilityService(db=db, redis=redis, now=now)

    await svc.is_supported("schwab", "MARKET", "DAY")
    await svc.is_supported("schwab", "MARKET", "DAY")
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_drops_broker_cache() -> None:
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    redis = MagicMock()
    svc = OrderCapabilityService(db=db, redis=redis)

    await svc.is_supported("schwab", "MARKET", "DAY")
    svc.invalidate("schwab")
    await svc.is_supported("schwab", "MARKET", "DAY")
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_pubsub_failure_falls_back_to_local_invalidate() -> None:
    """MED-5: Redis pubsub failure increments metric and still invalidates locally."""
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    redis = MagicMock()
    redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    svc = OrderCapabilityService(db=db, redis=redis)

    await svc.is_supported("schwab", "MARKET", "DAY")
    db.execute.reset_mock()
    await svc.publish_invalidation("schwab")
    # Local cache was busted as defense-in-depth.
    await svc.is_supported("schwab", "MARKET", "DAY")
    assert db.execute.await_count == 1, "local cache should have been busted on pubsub failure"
