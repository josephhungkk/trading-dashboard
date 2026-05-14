from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_read_bucket_allows_up_to_10() -> None:
    mock_redis = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=9)
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is True


@pytest.mark.asyncio
async def test_read_bucket_blocks_at_limit() -> None:
    mock_redis = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=10)
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is False


@pytest.mark.asyncio
async def test_write_bucket_allows_under_limit() -> None:
    mock_redis = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=2)
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_write(chat_id=111, from_user_id=222)
    assert allowed is True


@pytest.mark.asyncio
async def test_redis_unavailable_fails_open() -> None:
    mock_redis = AsyncMock()
    mock_redis.zadd = AsyncMock(side_effect=ConnectionError("redis down"))
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    rl = TelegramRateLimiter(redis=mock_redis)
    allowed = await rl.check_read(chat_id=111, from_user_id=222)
    assert allowed is True


@pytest.mark.asyncio
async def test_check_trade_bucket_independent() -> None:
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=0)
    mock_redis.zadd = AsyncMock()
    mock_redis.expire = AsyncMock()

    limiter = TelegramRateLimiter(redis=mock_redis)
    # Write bucket: exhaust it (3 calls)
    for _ in range(3):
        await limiter.check_write(chat_id=1, from_user_id=2)

    # Trade bucket is independent — should still pass
    result = await limiter.check_trade(chat_id=1, from_user_id=2)
    assert result is True

    # Verify trade key used, not write key
    trade_key_calls = [
        str(c) for c in mock_redis.zremrangebyscore.call_args_list if "trade" in str(c)
    ]
    assert len(trade_key_calls) > 0


@pytest.mark.asyncio
async def test_check_trade_fails_closed_on_redis_error() -> None:
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock(side_effect=Exception("Redis down"))

    limiter = TelegramRateLimiter(redis=mock_redis)
    result = await limiter.check_trade(chat_id=1, from_user_id=2)
    assert result is False  # fail-CLOSED for trade bucket


@pytest.mark.asyncio
async def test_check_write_still_fails_open_on_redis_error() -> None:
    from app.services.telegram.rate_limiter import TelegramRateLimiter

    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock(side_effect=Exception("Redis down"))

    limiter = TelegramRateLimiter(redis=mock_redis)
    result = await limiter.check_write(chat_id=1, from_user_id=2)
    assert result is True  # existing buckets remain fail-open
