from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.telegram.bot import telegram_shutdown, telegram_startup

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_telegram_startup_calls_set_webhook() -> None:
    bot = MagicMock()
    bot.set_webhook = AsyncMock(return_value=None)

    with patch("app.services.telegram.bot.Bot", return_value=bot):
        returned = await telegram_startup(
            bot_token="token",
            webhook_secret="secret",
            webhook_url="https://example.test/api/telegram/webhook",
        )

    assert returned is bot
    bot.set_webhook.assert_awaited_once_with(
        url="https://example.test/api/telegram/webhook",
        secret_token="secret",
    )


@pytest.mark.asyncio
async def test_telegram_shutdown_does_not_call_delete_webhook() -> None:
    bot = MagicMock()
    bot.session.close = AsyncMock(return_value=None)
    bot.delete_webhook = AsyncMock(return_value=None)

    await telegram_shutdown(bot)

    bot.session.close.assert_awaited_once()
    bot.delete_webhook.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_startup_retries_set_webhook_on_failure() -> None:
    bot = MagicMock()
    bot.set_webhook = AsyncMock(side_effect=[Exception("one"), Exception("two"), None])

    with (
        patch("app.services.telegram.bot.Bot", return_value=bot),
        patch("app.services.telegram.bot.asyncio.sleep", new=AsyncMock()),
    ):
        returned = await telegram_startup(
            bot_token="token",
            webhook_secret="secret",
            webhook_url="https://example.test/api/telegram/webhook",
        )

    assert returned is bot
    assert bot.set_webhook.await_count == 3
