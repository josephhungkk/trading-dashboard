from __future__ import annotations

import json
from typing import Any, Protocol

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome


class _RedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> int: ...


class InAppChannel(AlertChannel):
    name = "in_app"

    def __init__(self, *, redis: _RedisLike) -> None:
        self._redis = redis

    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome:
        payload = json.dumps(
            {
                "v": 1,
                "type": "fire",
                "fire_id": fire.fire_id,
                "alert_id": fire.alert_id,
                "user_label": fire.user_label,
                "verdict": fire.verdict,
                "evaluated_values": fire.evaluated_values,
                "fired_at": fire.fired_at_iso,
            }
        )
        await self._redis.publish(f"alerts:fire:{fire.jwt_subject}", payload)
        return DeliveryOutcome.sent
