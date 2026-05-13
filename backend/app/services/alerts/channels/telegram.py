"""Stub channel — wired at 11c."""

from __future__ import annotations

from typing import Any

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome


class TelegramChannel(AlertChannel):
    name = "telegram"

    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome:
        return DeliveryOutcome.channel_unavailable
