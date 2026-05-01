"""Phase 7a C5 — smoke tests for admin Schwab OAuth routes."""

import pytest


@pytest.mark.asyncio
async def test_oauth_start_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.get("/api/admin/brokers/schwab/oauth-start")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_callback_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.post(
        "/api/admin/brokers/schwab/oauth-callback",
        params={"code": "C", "state": "S"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.get("/api/admin/brokers/schwab/status")
    assert resp.status_code == 401
