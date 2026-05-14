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
