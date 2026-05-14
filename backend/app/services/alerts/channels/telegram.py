"""TelegramChannel - outbound alert delivery via Telegram Bot API."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Any

import structlog

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome

if TYPE_CHECKING:
    from aiogram import Bot

    from app.services.telegram.allowlist import AllowlistService

log = structlog.get_logger(__name__)
_SEND_TIMEOUT = 5.0


def _format_message(fire: AlertFire, public_base_url: str) -> str:
    label = html.escape(fire.user_label)
    verdict = html.escape(fire.verdict)
    values = html.escape(str(fire.evaluated_values))
    url = f"{public_base_url}/alerts/{fire.alert_id}"
    return (
        f'🔔 <b>{label}</b>\nVerdict: {verdict}\nValue: {values}\n<a href="{url}">View alert →</a>'
    )


class TelegramChannel(AlertChannel):
    name = "telegram"

    def __init__(
        self,
        *,
        bot: Bot | None,
        allowlist: AllowlistService,
        public_base_url: str,
    ) -> None:
        self._bot = bot
        self._allowlist = allowlist
        self._public_base_url = public_base_url

    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome:
        if self._bot is None:
            return DeliveryOutcome.channel_unavailable
        bot = self._bot
        chat_ids = self._allowlist.all_chat_ids()
        if not chat_ids:
            return DeliveryOutcome.channel_unavailable
        text = _format_message(fire, self._public_base_url)

        async def send_one(chat_id: int) -> bool:
            try:
                await asyncio.wait_for(
                    bot.send_message(chat_id=chat_id, text=text),
                    timeout=_SEND_TIMEOUT,
                )
                return True
            except Exception as exc:
                log.warning(
                    "telegram.send_failed",
                    alert_id=fire.alert_id,
                    chat_id=chat_id,
                    error_class=type(exc).__name__,
                )
                return False

        raw_results = await asyncio.gather(
            *[send_one(cid) for cid in chat_ids],
            return_exceptions=True,
        )
        successes = sum(1 for r in raw_results if r is True)
        failures = len(raw_results) - successes
        if failures:
            log.warning(
                "telegram.partial_send_failures",
                alert_id=fire.alert_id,
                failures=failures,
                total=len(raw_results),
            )
        if successes == 0:
            return DeliveryOutcome.failed
        return DeliveryOutcome.sent
