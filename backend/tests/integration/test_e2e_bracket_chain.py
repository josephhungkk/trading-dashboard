"""End-to-end bracket chain test (5c E3).

Drives the preview -> POST /api/orders/bracket -> DELETE parent -> assert
OCA cascade chain through the FastAPI ASGITransport against the extended
sidecar mock servicer (E1: PlaceBracket + cascade-aware CancelOrder).
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


@pytest.mark.asyncio
async def test_full_bracket_chain(client: AsyncClient) -> None:
    """4-step chain: enable -> preview -> bracket -> cancel-cascade -> revert."""
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
    paper = [a for a in r.json()["accounts"] if a.get("mode") == "paper"]
    assert paper, "no paper accounts in test fixture"
    acct_id = paper[0]["id"]

    preview_body = {
        "account_id": acct_id,
        "conid": "265598",
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "DAY",
        "qty": "1",
        "limit_price": "50",
    }

    r = await client.post("/api/orders/preview", json=preview_body)
    assert r.status_code == 200, f"preview failed: {r.text}"
    nonce = r.json()["nonce"]

    coid = str(uuid.uuid4())
    r = await client.post(
        "/api/orders/bracket",
        json={
            **preview_body,
            "client_order_id": coid,
            "nonce": nonce,
            "stop_price": "45",
            "target_price": "55",
        },
    )
    assert r.status_code == 200, f"bracket failed: {r.text}"
    body = r.json()
    parent_id = body["parent"]["id"]
    assert body["parent"]["broker_order_id"]
    assert len(body["children"]) == 2
    for child in body["children"]:
        assert child["broker_order_id"]
    assert body["oca_group"].startswith("BRK-")

    child_ids = [child["id"] for child in body["children"]]

    r = await client.delete(f"/api/orders/{parent_id}")
    assert r.status_code == 202

    deadline = 50
    cancelled = {parent_id: False, **dict.fromkeys(child_ids, False)}
    for _ in range(deadline):
        for oid in [parent_id, *child_ids]:
            if cancelled[oid]:
                continue
            r = await client.get(f"/api/orders/{oid}")
            if r.status_code == 200 and r.json().get("status") == "cancelled":
                cancelled[oid] = True
        if all(cancelled.values()):
            break
        await asyncio.sleep(0.1)
    assert all(cancelled.values()), (
        f"OCA cascade did not complete within 5s; pending: "
        f"{[oid for oid, ok in cancelled.items() if not ok]}"
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
