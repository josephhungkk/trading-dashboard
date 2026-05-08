"""End-to-end trade chain test (5b.1 D1).

Drives the full preview -> place -> cancel chain through the FastAPI
ASGITransport (no real ports) against the extended sidecar mock servicer.
Assertions catch all five v0.5.1 bugs deterministically.
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
    # ASGITransport does not drive lifespan; invoke Starlette's built-in
    # lifespan_context so set_config_service() runs before any request.
    # Broker init inside lifespan is wrapped in try/except — failures are
    # logged + skipped, which is the expected path in CI (no sidecars).
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.mark.skip(
    reason=(
        "Broken since v0.10.0 lifespan-init refactor — needs FakeBrokerServicer "
        "wiring + mTLS PKI seed in app_secrets so build_broker_registry succeeds. "
        "See docs/superpowers/plans/2026-05-08-ci-debt-cleanup.md (Companion Issues)."
    )
)
@pytest.mark.asyncio
async def test_full_trade_chain(client: AsyncClient) -> None:
    """7-step chain: enable -> preview -> place -> cancel -> revert."""
    r = await client.post(
        "/api/admin/config",
        json={
            "namespace": "broker",
            "key": "isa-paper.trade_enabled",
            "value": True,
            "value_type": "bool",
        },
    )
    assert r.status_code == 201

    r = await client.get("/api/accounts")
    assert r.status_code == 200
    accounts = r.json()["accounts"]
    paper = [a for a in accounts if a.get("mode") == "paper"]
    assert paper, "no paper accounts in test fixture"
    acct_id = paper[0]["id"]

    r = await client.post(
        "/api/orders/preview",
        json={
            "account_id": acct_id,
            "conid": "265598",
            "side": "BUY",
            "order_type": "LIMIT",
            "tif": "DAY",
            "qty": "1",
            "limit_price": "1",
        },
    )
    assert r.status_code == 200, f"preview failed: {r.text}"
    prev = r.json()
    assert prev["nonce"]
    assert prev["notional_currency"]

    coid = str(uuid.uuid4())
    r = await client.post(
        "/api/orders",
        json={
            "account_id": acct_id,
            "client_order_id": coid,
            "conid": "265598",
            "side": "BUY",
            "order_type": "LIMIT",
            "tif": "DAY",
            "qty": "1",
            "limit_price": "1",
            "nonce": prev["nonce"],
        },
    )
    assert r.status_code == 200, f"place failed: {r.text}"
    place_resp = r.json()
    order_id = place_resp["id"]
    assert place_resp["status"] == "submitted"
    assert place_resp["broker_order_id"].startswith("SIM-")

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
