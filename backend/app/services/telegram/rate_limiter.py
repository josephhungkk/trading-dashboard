"""Two-bucket sliding-window rate limiter for Telegram commands."""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_READ_LIMIT = 10
_WRITE_LIMIT = 3
_TRADE_LIMIT = 5
_WINDOW_SECONDS = 60


class TelegramRateLimiter:
    def __init__(self, *, redis: Any) -> None:
        self._redis = redis

    async def _check(self, key: str, limit: int, *, fail_closed: bool = False) -> bool:
        try:
            now = time.time()
            window_start = now - _WINDOW_SECONDS
            await self._redis.zremrangebyscore(key, "-inf", window_start)
            count = await self._redis.zcard(key)
            if count >= limit:
                return False
            await self._redis.zadd(key, {str(now): now})
            await self._redis.expire(key, _WINDOW_SECONDS + 5)
            return True
        except Exception:
            if fail_closed:
                log.warning("telegram.rate_limiter_redis_error_fail_closed", key=key)
                return False
            log.warning("telegram.rate_limiter_redis_error_fail_open", key=key)
            return True

    async def check_read(self, *, chat_id: int, from_user_id: int) -> bool:
        return await self._check(f"telegram:rl:read:{chat_id}:{from_user_id}", _READ_LIMIT)

    async def check_write(self, *, chat_id: int, from_user_id: int) -> bool:
        return await self._check(f"telegram:rl:write:{chat_id}:{from_user_id}", _WRITE_LIMIT)

    async def check_trade(self, *, chat_id: int, from_user_id: int) -> bool:
        return await self._check(
            f"telegram:rl:trade:{chat_id}:{from_user_id}",
            _TRADE_LIMIT,
            fail_closed=True,
        )
