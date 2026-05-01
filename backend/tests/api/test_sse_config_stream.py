"""Phase 7a C10 — smoke check for SSE config_stream route."""

import pytest


@pytest.mark.asyncio
async def test_sse_route_requires_admin_jwt(test_client_no_auth):
    """SSE config-stream is admin-gated."""
    resp = await test_client_no_auth.get(
        "/api/admin/config/stream",
        params={"ns": "schwab"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_route_rejects_invalid_ns(test_client_admin):
    """ns must match the regex (alphanumeric + underscore, 1-32 chars)."""
    resp = await test_client_admin.get(
        "/api/admin/config/stream",
        params={"ns": "Bad-Ns!"},
    )
    assert resp.status_code == 422
