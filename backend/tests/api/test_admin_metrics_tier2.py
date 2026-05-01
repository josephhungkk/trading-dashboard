"""Phase 7a E8 - POST /api/admin/metrics/tier2 sets the gauge + admin-gated."""

import pytest


@pytest.mark.asyncio
async def test_post_tier2_metric_requires_admin(test_client_no_auth):
    resp = await test_client_no_auth.post(
        "/api/admin/metrics/tier2",
        json={"last_run_seconds": 0.0},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_tier2_metric_route_mounted():
    from app.main import app

    routes = [r.path for r in app.routes]
    assert "/api/admin/metrics/tier2" in routes
