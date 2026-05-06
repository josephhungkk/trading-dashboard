import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.order_state_cache import OrderState, OrderStateCache


def _make_redis() -> MagicMock:
    redis = MagicMock()
    redis.hget = AsyncMock()
    redis.hset = AsyncMock()
    redis.hgetall = AsyncMock()
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis


def _make_cache(redis: MagicMock) -> OrderStateCache:
    return OrderStateCache(redis=redis, gateway_label="gw-1", account_id="acct-1")


def _cache_key() -> str:
    return "schwab:order_state:gw-1:acct-1"


@pytest.mark.asyncio
async def test_put_writes_to_redis_and_in_memory() -> None:
    redis = _make_redis()
    cache = _make_cache(redis)
    state = OrderState(
        client_order_id="cli-1",
        broker_order_id="brk-1",
        schwab_status="WORKING",
        entered_time_iso="2026-05-06T10:00:00Z",
    )

    await cache.put(state)
    result = await cache.get("cli-1")

    redis.hset.assert_awaited_once_with(
        _cache_key(), "cli-1", json.dumps(asdict(state))
    )
    redis.expire.assert_awaited_once_with(_cache_key(), 7 * 24 * 3600)
    redis.hget.assert_not_awaited()
    assert result == state


@pytest.mark.asyncio
async def test_get_falls_through_to_redis_on_miss() -> None:
    redis = _make_redis()
    cache = _make_cache(redis)
    state = OrderState(
        client_order_id="cli-1",
        broker_order_id="brk-1",
        schwab_status="FILLED",
        entered_time_iso="2026-05-06T10:00:00Z",
        last_exec_id="exec-1",
    )
    redis.hget.return_value = json.dumps(asdict(state)).encode()

    result = await cache.get("cli-1")

    redis.hget.assert_awaited_once_with(_cache_key(), "cli-1")
    assert result == state


@pytest.mark.asyncio
async def test_hydrate_from_redis_loads_all_keys() -> None:
    redis = _make_redis()
    cache = _make_cache(redis)
    state_1 = OrderState(
        client_order_id="cli-1",
        broker_order_id="brk-1",
        schwab_status="WORKING",
    )
    state_2 = OrderState(
        client_order_id="cli-2",
        broker_order_id="brk-2",
        schwab_status="FILLED",
        last_exec_id="exec-2",
    )
    redis.hgetall.return_value = {
        b"cli-1": json.dumps(asdict(state_1)).encode(),
        b"cli-2": json.dumps(asdict(state_2)).encode(),
    }

    await cache.hydrate()
    result_1 = await cache.get("cli-1")
    result_2 = await cache.get("cli-2")

    redis.hgetall.assert_awaited_once_with(_cache_key())
    redis.hget.assert_not_awaited()
    assert result_1 == state_1
    assert result_2 == state_2
    assert len(cache.known_client_order_ids()) == 2


@pytest.mark.asyncio
async def test_invalidate_all_clears_memory_and_redis() -> None:
    redis = _make_redis()
    cache = _make_cache(redis)
    state = OrderState(
        client_order_id="cli-1",
        broker_order_id="brk-1",
        schwab_status="WORKING",
    )

    await cache.put(state)
    await cache.invalidate_all()

    assert cache._mem == {}
    redis.delete.assert_awaited_once_with(_cache_key())
