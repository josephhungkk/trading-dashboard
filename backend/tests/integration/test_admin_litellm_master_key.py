"""Phase 11a-A.5 Task 14 — LiteLLM master key admin rotation endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis as fakeredis_async
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.cf_access import AdminIdentity
from app.core.db import SessionLocal
from app.core.deps import get_config, get_redis, require_admin_jwt
from app.main import app
from app.services.config import ConfigService


@pytest.fixture
async def admin_client(
    config_service: ConfigService,
    redis: fakeredis_async.FakeRedis,
) -> AsyncIterator[AsyncClient]:
    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="litellm-rotation@test.local", kind="user", claims={})

    def override_config() -> ConfigService:
        return config_service

    def override_redis() -> fakeredis_async.FakeRedis:
        return redis

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_redis] = override_redis

    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM app_secrets WHERE namespace = 'ai' AND key = 'litellm_master_key'")
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        async with SessionLocal() as session:
            await session.execute(
                text(
                    "DELETE FROM app_secrets WHERE namespace = 'ai' AND key = 'litellm_master_key'"
                )
            )
            await session.commit()
        app.dependency_overrides.clear()


async def _mint_nonce(client: AsyncClient) -> str:
    response = await client.post("/api/admin/csrf/issue")
    assert response.status_code == 200, response.text
    return str(response.json()["nonce"])


@pytest.mark.asyncio
async def test_put_litellm_master_key_writes_secret_and_redis(
    admin_client: AsyncClient,
    config_service: ConfigService,
    redis: fakeredis_async.FakeRedis,
) -> None:
    new_value = "litellm-master-key-rotated-alpha-001"
    nonce = await _mint_nonce(admin_client)

    response = await admin_client.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": new_value},
        headers={"X-Confirm-Nonce": nonce},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert await config_service.reveal_secret("ai", "litellm_master_key") == new_value
    assert await redis.get("ai:litellm_master_key") == new_value.encode()


@pytest.mark.asyncio
async def test_put_litellm_master_key_requires_csrf(admin_client: AsyncClient) -> None:
    response = await admin_client.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": "litellm-master-key-without-nonce-001"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "missing_csrf"


@pytest.mark.asyncio
async def test_put_litellm_master_key_rejects_short_value(
    admin_client: AsyncClient,
) -> None:
    nonce = await _mint_nonce(admin_client)

    response = await admin_client.put(
        "/api/admin/secrets/ai/litellm_master_key",
        json={"value": "too-short"},
        headers={"X-Confirm-Nonce": nonce},
    )

    assert response.status_code == 422
