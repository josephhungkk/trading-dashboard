from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.forex import _get_risk_service
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_config, get_db, get_redis, require_admin_jwt
from app.main import app

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _identity() -> AdminIdentity:
    return AdminIdentity(email="fx@example.test", kind="cf_access_jwt", claims={})


class _Config:
    async def get(self, ns: str, key: str, default=None):
        return default


class _Registry:
    async def healthy_clients(self):
        return [AsyncMock()]


async def _db() -> AsyncIterator[AsyncMock]:
    yield AsyncMock()


@pytest.fixture
async def forex_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_jwt] = _identity
    app.dependency_overrides[get_config] = lambda: _Config()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_broker_registry] = lambda: _Registry()
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


async def test_get_pairs_returns_default(forex_client: AsyncClient) -> None:
    response = await forex_client.get("/api/forex/pairs")
    assert response.status_code == 200
    assert "EURUSD" in response.json()["pairs"]


async def test_post_quote_missing_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/api/forex/quote",
        json={
            "pair": "EURUSD",
            "notional": "1000",
            "notional_currency": "base",
            "account_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 401


async def test_accept_nonce_expired(forex_client: AsyncClient) -> None:
    redis = AsyncMock()
    redis.getdel.return_value = None
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[_get_risk_service] = lambda: AsyncMock()

    response = await forex_client.post(
        "/api/forex/quote/bq-001/accept",
        json={
            "account_id": "00000000-0000-0000-0000-000000000001",
            "side": "BUY",
            "qty": "1000",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "nonce_expired_or_invalid"
