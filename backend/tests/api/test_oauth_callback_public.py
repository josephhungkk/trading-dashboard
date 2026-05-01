"""Phase 7a C4 — public callback path is reachable WITHOUT admin JWT."""

import pytest


@pytest.mark.asyncio
async def test_public_callback_invalid_state_returns_403(test_client_no_auth):
    resp = await test_client_no_auth.get(
        "/api/oauth/schwab/callback",
        params={"code": "AUTH_CODE", "state": "tampered.state"},
    )
    assert resp.status_code == 403
