"""Unit tests for AlgoCapabilityService."""

import json
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.services.algo.capability_service import AlgoCapabilityService

pytestmark = pytest.mark.no_db


@pytest_asyncio.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def svc(fake_redis):
    mock_db = AsyncMock()
    yield AlgoCapabilityService(redis=fake_redis, db=mock_db)


@pytest.mark.asyncio
async def test_get_strategies_unknown_broker(svc):
    result = await svc.get_strategies("unknownbroker", "STOCK")
    assert result == []


@pytest.mark.asyncio
async def test_cache_hit(svc, fake_redis):
    cache_key = "algo_cap:ibkr:STOCK"
    payload = json.dumps([{"algo_strategy": "TWAP", "enabled": True, "notes": ""}])
    await fake_redis.setex(cache_key, 300, payload.encode())
    result = await svc.get_strategies("ibkr", "STOCK")
    assert any(r["algo_strategy"] == "TWAP" for r in result)


@pytest.mark.asyncio
async def test_pubsub_invalidate_exact_key(svc, fake_redis):
    cache_key = "algo_cap:ibkr:STOCK"
    payload = json.dumps([{"algo_strategy": "TWAP", "enabled": True, "notes": ""}])
    await fake_redis.setex(cache_key, 300, payload.encode())
    await svc._handle_invalidation(json.dumps({"broker_id": "ibkr", "asset_class": "STOCK"}))
    assert await fake_redis.get(cache_key) is None


@pytest.mark.asyncio
async def test_pubsub_invalidate_malformed_increments_counter(svc, fake_redis):
    from app.core import metrics

    before = metrics.algo_capability_invalidate_malformed_total._value.get()
    await svc._handle_invalidation("not-json")
    after = metrics.algo_capability_invalidate_malformed_total._value.get()
    assert after > before


@pytest.mark.asyncio
async def test_pubsub_flush_all(svc, fake_redis):
    await fake_redis.setex("algo_cap:ibkr:STOCK", 300, b"x")
    await fake_redis.setex("algo_cap:ibkr:OPTION", 300, b"x")
    await svc._handle_invalidation(json.dumps({}))
    keys = await fake_redis.keys("algo_cap:*")
    assert keys == []
