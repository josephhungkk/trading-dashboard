"""Tests for config_cache — in-memory dict + TTL + pub/sub listener."""

import asyncio

import fakeredis.aioredis as fakeredis_async
import pytest

from app.core.metrics import registry  # noqa: F401
from app.services.config_cache import ConfigCache


@pytest.fixture
async def redis():
    r = fakeredis_async.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_cache_hit_miss(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    assert cache.get(("telegram", "bot_token")) is None
    cache.set(("telegram", "bot_token"), "abc")
    assert cache.get(("telegram", "bot_token")) == "abc"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=0
    )
    cache.set(("ns", "k"), "v")
    assert cache.get(("ns", "k")) is None


@pytest.mark.asyncio
async def test_cache_pop(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    cache.set(("a", "b"), "x")
    assert cache.get(("a", "b")) == "x"
    cache.pop(("a", "b"))
    assert cache.get(("a", "b")) is None


@pytest.mark.asyncio
async def test_publish_invalidation(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    pubsub = redis.pubsub()
    await pubsub.subscribe("config:invalidate")
    await asyncio.sleep(0.05)

    await cache.publish_invalidation("telegram", "bot_token")
    await asyncio.sleep(0.05)

    msgs = []
    async for msg in pubsub.listen():
        if msg["type"] == "message":
            msgs.append(msg["data"])
            break
    assert b"telegram|bot_token" in msgs
    await pubsub.unsubscribe("config:invalidate")
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_publish_swallows_errors(redis, caplog):
    import logging
    from unittest.mock import AsyncMock

    cache = ConfigCache(
        redis=redis, channel="config:invalidate", kind_label="config", ttl_seconds=60
    )
    cache.redis.publish = AsyncMock(side_effect=ConnectionError("no redis"))
    with caplog.at_level(logging.WARNING, logger="app.services.config_cache"):
        await cache.publish_invalidation("ns", "k")
    assert any("publish failed" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_listener_evicts_on_message(redis):
    cache = ConfigCache(
        redis=redis, channel="config:invalidate:test", kind_label="config", ttl_seconds=60
    )
    cache.set(("ns", "key"), "stale")

    task = asyncio.create_task(cache.run_listener())
    await asyncio.sleep(0.1)

    await redis.publish("config:invalidate:test", b"ns|key")
    await asyncio.sleep(0.15)

    assert cache.get(("ns", "key")) is None

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
