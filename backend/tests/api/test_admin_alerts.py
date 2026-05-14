from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, require_admin_jwt
from app.main import app

pytestmark = pytest.mark.no_db


@pytest_asyncio.fixture
async def admin_alerts_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    config = AsyncMock()
    config.set = AsyncMock(return_value=None)
    config.set_secret = AsyncMock(return_value=None)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="admin@example.test",
        kind="cf_access_jwt",
        claims={},
    )
    app.dependency_overrides[get_config] = lambda: config

    async def no_csrf() -> None:
        return None

    app.dependency_overrides[consume_confirmation_nonce] = no_csrf
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_webhook_config(admin_alerts_client: AsyncClient) -> None:
    resp = await admin_alerts_client.put(
        "/api/admin/alerts/webhooks/1",
        json={"url": "https://hook.example.com/1", "secret": "mysecret"},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_id"] == 1
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_put_webhook_config_no_secret(admin_alerts_client: AsyncClient) -> None:
    resp = await admin_alerts_client.put(
        "/api/admin/alerts/webhooks/2",
        json={"url": "https://hook.example.com/2"},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_id"] == 2
