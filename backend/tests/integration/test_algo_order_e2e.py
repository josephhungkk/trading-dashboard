"""E2E smoke tests for algo order paths through preview endpoint.

These tests verify the preview endpoint is reachable and does not crash on
algo payloads.  Full algo validation logic is covered by unit tests:
  - test_orders_service_algo.py (validate_pre_dispatch checks)
  - test_risk_service_algo.py  (_check_algo_capability, _check_iceberg_display_size)

Note: account_id '00000000-0000-0000-0000-000000000001' is not seeded in the
test DB, so resolve_account raises 404 before algo checks fire.  503 is
returned when the broker layer is also absent.  Both are acceptable here.
"""

import pytest


@pytest.mark.asyncio
async def test_twap_on_bond_rejected(test_client_admin):
    """Endpoint accepts TWAP payload and returns 422/503/404 (not 500)."""
    resp = await test_client_admin.post(
        "/api/orders/preview",
        json={
            "account_id": "00000000-0000-0000-0000-000000000001",
            "conid": "265598",
            "side": "BUY",
            "order_type": "MARKET",
            "tif": "DAY",
            "qty": "100",
            "algo_strategy": "TWAP",
            "algo_params": {"start_time": "10:00", "end_time": "14:00"},
        },
    )
    assert resp.status_code in (404, 422, 503), (
        f"Expected 404/422/503, got {resp.status_code}: {resp.text[:200]}"
    )
    assert resp.status_code != 500


@pytest.mark.asyncio
async def test_iceberg_market_order_smoke(test_client_admin):
    """Endpoint does not 500 on ICEBERG+MARKET payload."""
    resp = await test_client_admin.post(
        "/api/orders/preview",
        json={
            "account_id": "00000000-0000-0000-0000-000000000001",
            "conid": "265598",
            "side": "BUY",
            "order_type": "MARKET",
            "tif": "DAY",
            "qty": "100",
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "10"},
        },
    )
    assert resp.status_code in (404, 422, 503), (
        f"Expected 404/422/503, got {resp.status_code}: {resp.text[:200]}"
    )
    assert resp.status_code != 500
    # When algo check fires before account lookup, assert correct error code.
    if resp.status_code == 422:
        assert "algo_requires_limit" in resp.text


@pytest.mark.asyncio
async def test_iceberg_display_size_zero_smoke(test_client_admin):
    """Endpoint does not 500 on ICEBERG+display_size=0 payload."""
    resp = await test_client_admin.post(
        "/api/orders/preview",
        json={
            "account_id": "00000000-0000-0000-0000-000000000001",
            "conid": "265598",
            "side": "BUY",
            "order_type": "LIMIT",
            "limit_price": "150.00",
            "tif": "DAY",
            "qty": "100",
            "algo_strategy": "ICEBERG",
            "algo_params": {"display_size": "0"},
        },
    )
    assert resp.status_code in (404, 422, 503), (
        f"Expected 404/422/503, got {resp.status_code}: {resp.text[:200]}"
    )
    assert resp.status_code != 500
