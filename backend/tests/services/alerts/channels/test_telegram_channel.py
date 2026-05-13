from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.alerts.channels.telegram import TelegramChannel
from app.services.alerts.delivery import AlertFire, DeliveryOutcome

pytestmark = pytest.mark.no_db


def _fire() -> AlertFire:
    return AlertFire(
        fire_id=1,
        alert_id=2,
        jwt_subject="user@example.test",
        verdict="AAPL > 200",
        evaluated_values={"AAPL": 201.5},
        user_label="AAPL alert",
        fired_at_iso="2026-05-13T10:00:00Z",
    )


def _allowlist(chat_ids: list[int]) -> MagicMock:
    allowlist = MagicMock()
    allowlist.all_chat_ids.return_value = chat_ids
    return allowlist


@pytest.mark.asyncio
async def test_deliver_bot_none_returns_channel_unavailable() -> None:
    channel = TelegramChannel(
        bot=None,
        allowlist=_allowlist([123]),
        public_base_url="https://example.test",
    )

    outcome = await channel.deliver(_fire(), {})

    assert outcome == DeliveryOutcome.channel_unavailable


@pytest.mark.asyncio
async def test_deliver_all_sent_returns_sent() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    channel = TelegramChannel(
        bot=bot,
        allowlist=_allowlist([123, 456]),
        public_base_url="https://example.test",
    )

    outcome = await channel.deliver(_fire(), {})

    assert outcome == DeliveryOutcome.sent
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_deliver_all_failed_returns_failed() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("send failed"))
    channel = TelegramChannel(
        bot=bot,
        allowlist=_allowlist([123, 456]),
        public_base_url="https://example.test",
    )

    outcome = await channel.deliver(_fire(), {})

    assert outcome == DeliveryOutcome.failed


@pytest.mark.asyncio
async def test_deliver_concurrent_not_serial() -> None:
    async def send_message(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=send_message)
    channel = TelegramChannel(
        bot=bot,
        allowlist=_allowlist(list(range(10))),
        public_base_url="https://example.test",
    )

    started = time.perf_counter()
    outcome = await channel.deliver(_fire(), {})
    elapsed = time.perf_counter() - started

    assert outcome == DeliveryOutcome.sent
    assert elapsed < 0.4


@pytest.mark.asyncio
async def test_deliver_partial_failure_returns_sent() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[None, RuntimeError("send failed")])
    channel = TelegramChannel(
        bot=bot,
        allowlist=_allowlist([123, 456]),
        public_base_url="https://example.test",
    )

    outcome = await channel.deliver(_fire(), {})

    assert outcome == DeliveryOutcome.sent
