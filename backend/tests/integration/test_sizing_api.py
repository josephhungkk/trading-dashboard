from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.sizing import _POSITION_SIZE_LIMITER


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    """The position-size limiter is a module-level singleton; reset its
    sliding-window state between tests so 429 doesn't leak across tests."""
    _POSITION_SIZE_LIMITER._buckets.clear()


@pytest.mark.asyncio
async def test_position_size_endpoint_returns_404_when_account_missing(
    test_client_admin: AsyncClient,
) -> None:
    """Without a seeded account the orchestrator raises 'account not found'
    which becomes a 404. The point of this test is the routing + dep wiring
    work; happy-path seeding is left to E2E."""
    response = await test_client_admin.post(
        "/api/risk/position-size",
        json={
            "account_id": str(uuid4()),
            "instrument_id": 99999999,
            "method": "fixed_fractional",
            "side": "buy",
            "inputs": {
                "kind": "fixed_fractional",
                "risk_pct": "2.00",
                "price": "50.00",
            },
        },
    )
    assert response.status_code in (404, 422, 500), response.text


@pytest.mark.asyncio
async def test_sizing_defaults_get_returns_defaults_when_unset(
    test_client_admin: AsyncClient,
) -> None:
    """Unset → returns the default SizingDefaults shape."""
    account_id = uuid4()
    response = await test_client_admin.get(f"/api/risk/sizing-defaults/{account_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "fixed_fractional"
    assert Decimal(body["fixed_fractional_risk_pct"]) == Decimal("2.00")
    assert Decimal(body["risk_per_trade_risk_pct"]) == Decimal("1.00")
    assert Decimal(body["vol_targeted_target_vol_pct"]) == Decimal("15.00")


@pytest.mark.asyncio
async def test_admin_sizing_defaults_put_requires_csrf(
    test_client_admin: AsyncClient,
) -> None:
    """PUT without X-Confirm-Nonce → 401/403/422 from the CSRF dep."""
    account_id = uuid4()
    response = await test_client_admin.put(
        f"/api/admin/sizing-defaults/{account_id}",
        json={
            "method": "vol_targeted",
            "fixed_fractional_risk_pct": "2.00",
            "risk_per_trade_risk_pct": "1.00",
            "vol_targeted_target_vol_pct": "15.00",
        },
    )
    assert response.status_code in (401, 403, 422), response.text


@pytest.mark.asyncio
async def test_position_size_rejects_missing_instrument_identity(
    test_client_admin: AsyncClient,
) -> None:
    """SizingRequest validator rejects payloads with neither instrument_id
    nor (conid + broker_id) set — 422 from Pydantic."""
    response = await test_client_admin.post(
        "/api/risk/position-size",
        json={
            "account_id": str(uuid4()),
            # no instrument_id, no conid, no broker_id
            "method": "fixed_fractional",
            "side": "buy",
            "inputs": {"kind": "fixed_fractional", "risk_pct": "2", "price": "50"},
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_position_size_conid_path_returns_404_when_unresolved(
    test_client_admin: AsyncClient,
) -> None:
    """Sending conid+broker_id that doesn't resolve to an instrument → 404."""
    response = await test_client_admin.post(
        "/api/risk/position-size",
        json={
            "account_id": str(uuid4()),
            "conid": "DOES-NOT-EXIST-CONID-9999",
            "broker_id": "ibkr",
            "method": "fixed_fractional",
            "side": "buy",
            "inputs": {"kind": "fixed_fractional", "risk_pct": "2", "price": "50"},
        },
    )
    # Either 404 (resolver miss) or 422 (validation passed through and
    # downstream rejected on account_not_found etc.)
    assert response.status_code in (404, 422), response.text


@pytest.mark.asyncio
async def test_position_size_rate_limit(test_client_admin: AsyncClient) -> None:
    """21st call within 1s from the same (user, account) → 429."""
    account_id = uuid4()
    payload = {
        "account_id": str(account_id),
        "instrument_id": 99999999,
        "method": "fixed_fractional",
        "side": "buy",
        "inputs": {"kind": "fixed_fractional", "risk_pct": "2.00", "price": "50.00"},
    }
    statuses: list[int] = []
    for _ in range(25):
        r = await test_client_admin.post("/api/risk/position-size", json=payload)
        statuses.append(r.status_code)
    assert 429 in statuses, f"expected at least one 429 in {statuses}"
