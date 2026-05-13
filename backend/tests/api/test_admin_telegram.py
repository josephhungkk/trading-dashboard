from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.main import app

pytestmark = pytest.mark.no_db


class _Rows:
    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return []


class _Db:
    async def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        return _Rows()


@pytest_asyncio.fixture
async def admin_telegram_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    config = AsyncMock()
    config.get = AsyncMock(return_value="https://example.test")
    config.get_json = AsyncMock(return_value=[])
    config.reveal_secret = AsyncMock(return_value=None)
    config.set = AsyncMock(return_value=None)
    config.set_secret = AsyncMock(return_value=None)
    redis = AsyncMock()
    redis.publish = AsyncMock(return_value=1)

    async def override_db() -> AsyncIterator[_Db]:
        yield _Db()

    app.state.telegram_webhook_status = "failed"
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="admin@example.test",
        kind="cf_access_jwt",
        claims={},
    )
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_db] = override_db

    async def no_csrf() -> None:
        return None

    app.dependency_overrides[consume_confirmation_nonce] = no_csrf
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_returns_200(admin_telegram_client: AsyncClient) -> None:
    response = await admin_telegram_client.get("/api/admin/telegram/config")

    assert response.status_code == 200
    body = response.json()
    assert "webhook_status" in body
    assert "webhook_url" in body


@pytest.mark.asyncio
async def test_get_allowlist_returns_list(admin_telegram_client: AsyncClient) -> None:
    response = await admin_telegram_client.get("/api/admin/telegram/allowlist")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_post_allowlist_adds_entry(admin_telegram_client: AsyncClient) -> None:
    response = await admin_telegram_client.post(
        "/api/admin/telegram/allowlist",
        json={
            "chat_id": 123,
            "from_user_id": 456,
            "jwt_subject": "user@example.test",
            "label": "primary",
        },
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_command_log_returns_list(admin_telegram_client: AsyncClient) -> None:
    response = await admin_telegram_client.get("/api/admin/telegram/command-log")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
