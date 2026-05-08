"""End-to-end modify chain test (5c E2).

Drives the full preview -> place -> preview-modify -> PUT modify -> cancel
chain through the FastAPI ASGITransport against the extended sidecar mock
servicer (E1: ModifyOrder handler).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async def _admin() -> AdminIdentity:
        return AdminIdentity(email="ci@example.com", kind="user", claims={})

    app.dependency_overrides[require_admin_jwt] = _admin
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.skip(
    reason=(
        "Broken by _app_state autouse which stubs account_service with a MagicMock — "
        "GET /api/accounts returns ResponseValidationError because the mock returns a "
        "non-serialisable MagicMock instead of AccountListResponse. Same root cause as "
        "test_e2e_trade_chain.py: needs FakeBrokerServicer wiring + real account_service "
        "so the accounts endpoint can return a real paper account row."
    )
)
@pytest.mark.asyncio
async def test_full_modify_chain(client: AsyncClient) -> None:
    """6-step chain: enable -> preview -> place -> preview-modify -> PUT -> cancel -> revert."""
    r = await client.post(
        "/api/admin/config",
        json={
            "namespace": "broker",
            "key": "isa-paper.trade_enabled",
            "value": True,
            "value_type": "bool",
        },
    )
    # 201 = first time this run; 409 = previous test in the same DB-shared
    # run already inserted the same key. Both leave the state we want.
    assert r.status_code in (201, 409), r.text

    r = await client.get("/api/accounts")
    assert r.status_code == 200
    paper = [a for a in r.json()["accounts"] if a.get("mode") == "paper"]
    assert paper, "no paper accounts in test fixture"
    acct_id = paper[0]["id"]

    body = {
        "account_id": acct_id,
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": "1",
        "limit_price": "1",
    }

    r = await client.post("/api/orders/preview", json=body)
    assert r.status_code == 200, f"preview failed: {r.text}"
    place_nonce = r.json()["nonce"]

    coid = str(uuid.uuid4())
    r = await client.post(
        "/api/orders",
        json={**body, "client_order_id": coid, "nonce": place_nonce},
    )
    assert r.status_code == 200, f"place failed: {r.text}"
    place_resp = r.json()
    order_id = place_resp["id"]
    assert place_resp["status"] == "submitted"

    r = await client.post("/api/orders/preview", json={**body, "qty": "2"})
    assert r.status_code == 200, f"preview-modify failed: {r.text}"
    modify_nonce = r.json()["nonce"]

    r = await client.put(
        f"/api/orders/{order_id}",
        json={"nonce": modify_nonce, "qty": "2", "limit_price": "1"},
    )
    assert r.status_code in (200, 202), f"modify failed: {r.text}"

    for _ in range(50):
        r = await client.get(f"/api/orders/{order_id}")
        if r.json()["status"] == "modified":
            break
        await asyncio.sleep(0.1)
    assert r.json()["status"] == "modified", (
        f"order did not reach modified within 5s; final: {r.json()}"
    )

    r = await client.delete(f"/api/orders/{order_id}")
    assert r.status_code == 202

    for _ in range(50):
        r = await client.get(f"/api/orders/{order_id}")
        if r.json()["status"] == "cancelled":
            break
        await asyncio.sleep(0.1)
    assert r.json()["status"] == "cancelled", (
        f"order did not transition to cancelled within 5s; final: {r.json()}"
    )

    r = await client.put(
        "/api/admin/config/broker/isa-paper.trade_enabled",
        json={
            "namespace": "broker",
            "key": "isa-paper.trade_enabled",
            "value": False,
            "value_type": "bool",
        },
    )
    assert r.status_code == 200
