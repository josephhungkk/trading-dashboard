"""Phase 7a F3 — real-Schwab smoke. Gated on CI_USE_REAL_SCHWAB=1.

Run nightly via .github/workflows/nightly-real-schwab.yml against the live
Schwab API using a long-lived test access token. Skipped in normal CI to
avoid rate-limits + token rotation.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CI_USE_REAL_SCHWAB") != "1",
    reason="Real-Schwab smoke disabled (set CI_USE_REAL_SCHWAB=1)",
)


@pytest.mark.asyncio
async def test_user_preference_endpoint_reachable():
    """GET /trader/v1/userPreference returns 200 with streamerInfo."""
    import httpx

    access = os.environ["SCHWAB_TEST_ACCESS_TOKEN"]
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(
            "https://api.schwabapi.com/trader/v1/userPreference",
            headers={"Authorization": f"Bearer {access}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "streamerInfo" in body
    assert isinstance(body["streamerInfo"], list)
    assert len(body["streamerInfo"]) >= 1


@pytest.mark.asyncio
async def test_account_numbers_endpoint_reachable():
    """GET /trader/v1/accounts/accountNumbers returns at least 1 account.

    Path corrected 2026-05-11: the live Schwab API uses
    `/trader/v1/accounts/accountNumbers` (with `/accounts/` in the middle),
    not `/trader/v1/accountNumbers`. The sidecar production path is via
    schwabdev.linked_accounts() which gets the correct URL internally; this
    test was a raw HTTP probe with the stale shorter form.
    """
    import httpx

    access = os.environ["SCHWAB_TEST_ACCESS_TOKEN"]
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(
            "https://api.schwabapi.com/trader/v1/accounts/accountNumbers",
            headers={"Authorization": f"Bearer {access}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    first = body[0]
    assert "accountNumber" in first
    assert "hashValue" in first
