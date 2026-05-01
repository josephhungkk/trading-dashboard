"""Phase 7a D4 — POST /disconnect smoke test (route auth gating)."""

import pytest


@pytest.mark.asyncio
async def test_disconnect_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.post(
        "/api/admin/brokers/schwab/disconnect",
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_disconnect_route_mounted():
    """Verify the route is on the FastAPI app."""
    from app.main import app

    routes = [r.path for r in app.routes]
    assert "/api/admin/brokers/schwab/disconnect" in routes
