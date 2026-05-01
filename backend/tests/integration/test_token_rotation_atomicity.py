"""Phase 7a F0 / C2 — concurrent Tier-1 + Tier-2 OAuth callbacks serialize via PG advisory lock.

v3.1 MED-7 — pin settings.app_secret_key so the route handlers use the same
HMAC key that the test uses to mint state nonces.
"""

import asyncio

import pytest


@pytest.fixture
def app_secret_key_pin(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_secret_key", "K", raising=False)


@pytest.mark.asyncio
async def test_concurrent_callbacks_serialized(
    app_secret_key_pin,
    test_client_no_auth,
    test_client_admin,
    redis,
    config_service,
    httpx_mock,
):
    """Concurrent Tier-1 + Tier-2 callbacks: both succeed, single-writer holds."""
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A1", "refresh_token": "R1", "expires_in": 1800},
    )
    httpx_mock.add_response(
        url="https://api.schwabapi.com/v1/oauth/token",
        method="POST",
        json={"access_token": "A2", "refresh_token": "R2", "expires_in": 1800},
    )

    from app.services.schwab_oauth import mint_state_nonce

    s1 = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    s2 = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    await config_service.set_secret("broker", "schwab.app_key", "K")
    await config_service.set_secret("broker", "schwab.app_secret", "S")

    async def call_public():
        return await test_client_no_auth.get(
            "/api/oauth/schwab/callback",
            params={"code": "C1", "state": s1},
        )

    async def call_admin():
        return await test_client_admin.post(
            "/api/admin/brokers/schwab/oauth-callback",
            params={"code": "C2", "state": s2},
        )

    r1, r2 = await asyncio.gather(call_public(), call_admin())
    assert r1.status_code == 200
    assert r2.status_code == 200
    final_token = await config_service.reveal_secret("broker", "schwab.refresh_token")
    final_access = await config_service.reveal_secret("broker", "schwab.access_token")
    # Coherent — both fields from the SAME run.
    assert (final_token, final_access) in (("R1", "A1"), ("R2", "A2"))
