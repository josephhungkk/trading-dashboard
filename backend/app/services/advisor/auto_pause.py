from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.services.advisor.metrics import (
    advisor_auto_pause_errors_total,
    advisor_auto_pause_triggered_total,
)
from app.services.advisor.types import AdvisorConfig

logger = structlog.get_logger(__name__)


class AutoPauseService:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def record_reject(self, *, bot_id: UUID, config: AdvisorConfig) -> None:
        key = f"bot:advisor:rejects:{bot_id}"
        try:
            now_ts = time.time()
            cutoff = now_ts - config.auto_pause_window_seconds
            await self._redis.zadd(key, {str(uuid4()): now_ts})
            await self._redis.zremrangebyscore(key, "-inf", cutoff)
            count = await self._redis.zcount(key, "-inf", "+inf")
            if config.auto_pause_threshold > 0 and count >= config.auto_pause_threshold:
                payload = json.dumps(
                    {"id": str(uuid4()), "cmd": "PAUSE", "reason": "advisor_auto_pause"}
                )
                await self._redis.xadd(f"bot:control:{bot_id}", {"data": payload})
                advisor_auto_pause_triggered_total.labels(bot_id=str(bot_id)).inc()
                logger.info("advisor_auto_pause_triggered", bot_id=str(bot_id), count=count)
        except Exception:
            advisor_auto_pause_errors_total.inc()
            logger.warning("advisor_auto_pause_redis_error", bot_id=str(bot_id), exc_info=True)
