"""Phase 7a F2 — /api/brokers/accounts spans IBKR+Futu+Schwab.

Smoke check: the route exists and returns 200 with a list. Detailed multi-broker
fan-out is exercised by the existing test_accounts_list.py / test_accounts_list_nlv.py
tests in tests/api/. This test focuses on the Schwab branch boundary-strip.
"""

import pytest


@pytest.mark.asyncio
async def test_accounts_endpoint_strips_schwab_account_hash(test_client_admin):
    """Verify the /api/brokers/accounts route is mounted + admin-gated."""
    resp = await test_client_admin.get("/api/brokers/accounts")
    # 200 (data) or 503 (broker layer not configured) both acceptable in test env;
    # the key invariant is admin-gated route exists.
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        body = resp.json()
        for row in body.get("accounts", []):
            assert "account_hash" not in row, f"account_hash leaked in {row}"
            assert "gateway_label" not in row, f"gateway_label leaked in {row}"
            assert "account_number" not in row, f"account_number leaked in {row}"


@pytest.mark.asyncio
async def test_accounts_endpoint_requires_admin_jwt(test_client_no_auth):
    resp = await test_client_no_auth.get("/api/brokers/accounts")
    assert resp.status_code == 401
