from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DeliveryOutcome(Enum):
    sent = "sent"
    failed = "failed"
    throttled = "throttled"
    channel_unavailable = "channel_unavailable"


@dataclass(slots=True)
class AlertFire:
    fire_id: int
    alert_id: int
    jwt_subject: str
    verdict: str
    evaluated_values: dict[str, Any]
    user_label: str
    fired_at_iso: str = ""


class AlertChannel(ABC):
    name: str

    @abstractmethod
    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome: ...


class DeliveryDispatcher:
    def __init__(self, *, channels: dict[str, AlertChannel]) -> None:
        self._channels = channels

    async def fan_out(
        self,
        fire: AlertFire,
        *,
        channel_keys: list[str],
        channel_configs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, DeliveryOutcome]:
        configs = channel_configs or {}
        outcomes: dict[str, DeliveryOutcome] = {}
        for key in channel_keys:
            base_key = key.split(":", 1)[0]
            channel = self._channels.get(base_key)
            if channel is None:
                outcomes[key] = DeliveryOutcome.channel_unavailable
                continue
            try:
                outcomes[key] = await channel.deliver(fire, configs.get(key, {}))
            except Exception:
                outcomes[key] = DeliveryOutcome.failed
        return outcomes
