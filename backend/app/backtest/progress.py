from __future__ import annotations

import json
from typing import Any


class ProgressPublisher:
    def __init__(self, *, redis: Any, backtest_id: str) -> None:
        self._redis = redis
        self._channel = f"backtest:progress:{backtest_id}"

    async def publish(
        self, current: int, total: int, trades_so_far: int, current_bar_ts: str
    ) -> None:
        pct = int(current / total * 100) if total > 0 else 0
        frame = {
            "type": "progress",
            "pct": pct,
            "trades_so_far": trades_so_far,
            "current_bar_ts": current_bar_ts,
        }
        await self._redis.publish(self._channel, json.dumps(frame))

    async def publish_done(self, report: dict) -> None:
        await self._redis.publish(self._channel, json.dumps({"type": "done", "report": report}))

    async def publish_failed(self, error_msg: str) -> None:
        await self._redis.publish(
            self._channel, json.dumps({"type": "failed", "error_msg": error_msg})
        )
