from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import telegram as telegram_api

pytestmark = pytest.mark.no_db


@pytest_asyncio.fixture
async def telegram_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(telegram_api.router)
    app.state.telegram_webhook_secret = "secret"
    app.state.telegram_bot = MagicMock()
    app.state.redis = AsyncMock()
    app.state.redis.get = AsyncMock(return_value=None)
    app.state.redis.set = AsyncMock(return_value=True)

    old_dp = telegram_api.dp
    telegram_api.dp = MagicMock()
    telegram_api.dp.feed_update = AsyncMock(return_value=None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        telegram_api.dp = old_dp


def _headers(token: str = "secret") -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": token}


def _body(update_id: int = 123) -> dict[str, object]:
    return {"update_id": update_id}


@pytest.mark.asyncio
async def test_webhook_invalid_token_returns_403(telegram_client: AsyncClient) -> None:
    response = await telegram_client.post(
        "/api/telegram/webhook",
        headers=_headers("wrong"),
        json=_body(),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_bot_none_returns_503(telegram_client: AsyncClient) -> None:
    telegram_client._transport.app.state.telegram_bot = None  # type: ignore[attr-defined]

    response = await telegram_client.post(
        "/api/telegram/webhook",
        headers=_headers(),
        json=_body(),
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_webhook_duplicate_update_id_is_noop(telegram_client: AsyncClient) -> None:
    telegram_client._transport.app.state.redis.set = AsyncMock(return_value=None)  # type: ignore[attr-defined]  # NX miss → already exists

    response = await telegram_client.post(
        "/api/telegram/webhook",
        headers=_headers(),
        json=_body(),
    )

    assert response.status_code == 200
    telegram_api.dp.feed_update.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_valid_token_returns_200(telegram_client: AsyncClient) -> None:
    response = await telegram_client.post(
        "/api/telegram/webhook",
        headers=_headers(),
        json=_body(),
    )

    assert response.status_code == 200
    telegram_api.dp.feed_update.assert_awaited_once()
