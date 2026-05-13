"""Bot + Dispatcher singletons and lifespan helpers."""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

log = structlog.get_logger(__name__)
_RETRY_DELAYS = (1.0, 3.0, 9.0)


def build_dispatcher() -> Dispatcher:
    return Dispatcher()


async def _set_webhook_with_retry(bot: Bot, *, url: str, secret_token: str) -> bool:
    for attempt, delay in enumerate((0.0, *_RETRY_DELAYS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await bot.set_webhook(
                url=url,
                secret_token=secret_token,
            )
            log.info("telegram.set_webhook_ok", url=url)
            return True
        except Exception as exc:
            log.warning(
                "telegram.set_webhook_failed",
                attempt=attempt,
                error_class=type(exc).__name__,
                error=str(exc),
            )
    return False


async def telegram_startup(*, bot_token: str, webhook_secret: str, webhook_url: str) -> Bot:
    bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    ok = await _set_webhook_with_retry(bot, url=webhook_url, secret_token=webhook_secret)
    if not ok:
        log.error("telegram.set_webhook_all_retries_failed")
    return bot


async def telegram_shutdown(bot: Bot) -> None:
    await bot.session.close()
