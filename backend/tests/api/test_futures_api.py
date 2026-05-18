from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


def _fake_identity() -> AdminIdentity:
    return AdminIdentity(email="test@example.com", kind="cf-access")


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def authed_client():
    app.dependency_overrides[require_admin_jwt] = _fake_identity
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.pop(require_admin_jwt, None)


class TestFuturesAPI:
    @pytest.mark.asyncio
    async def test_get_contracts_requires_jwt(self, client):
        response = await client.get("/api/futures/contracts/ES")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_contracts_returns_list(self, authed_client):
        with patch("app.api.futures._get_resolver") as mock_resolver_dep:
            mock_resolver = AsyncMock()
            mock_resolver.get_contracts = AsyncMock(return_value=[])
            mock_resolver_dep.return_value = mock_resolver

            response = await authed_client.get("/api/futures/contracts/ES")
            assert response.status_code == 200
            assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_get_roll_rules_requires_jwt(self, client):
        response = await client.get("/api/futures/roll-rules")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_roll_confirm_requires_csrf(self, authed_client):
        nonce = str(uuid4())
        response = await authed_client.post(f"/api/futures/roll/confirm/{nonce}")
        assert response.status_code in [403, 422]

    @pytest.mark.asyncio
    async def test_roll_confirm_missing_nonce_returns_404(self, authed_client):
        nonce = str(uuid4())
        account_id = str(uuid4())
        with patch("app.api.futures._get_roll_service") as mock_service_dep:
            mock_service = AsyncMock()
            mock_service.execute_roll = AsyncMock(side_effect=KeyError("Not found"))
            mock_service_dep.return_value = mock_service

            response = await authed_client.post(
                f"/api/futures/roll/confirm/{nonce}",
                params={"account_id": account_id},
                headers={"X-Csrf-Nonce": nonce},
            )
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_settlements_requires_jwt(self, client):
        response = await client.get("/api/futures/settlements")
        assert response.status_code == 401
