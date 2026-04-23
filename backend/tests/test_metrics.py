"""Tests for /metrics endpoint."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="t@x", kind="user", claims={}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_metrics_returns_prometheus_text_with_dep_override(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "cf_jwt_verification_total" in body
    assert "config_ops_total" in body
