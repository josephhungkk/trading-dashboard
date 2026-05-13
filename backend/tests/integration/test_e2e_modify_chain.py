"""End-to-end modify chain test (5c E2).

Drives the full preview -> place -> preview-modify -> PUT modify -> cancel
chain through the FastAPI ASGITransport against the extended sidecar mock
servicer (E1: ModifyOrder handler).

Phase 11a CI-debt sweep (2026-05-12): unskipped after the
``e2e_chain.chain_client`` fixture landed (commit 59d4c08) and the two
real risk-gate bugs it surfaced were fixed (commit e7e9fa0).

Phase 11b (2026-05-13): cancel-and-replace race fixed by teaching
``OrderEventConsumer`` to short-circuit on ``event.kind == "replaced"``
— the audit row is still written, but the row UPDATE + WS publish are
skipped so ``orders_service.modify_order`` retains control of the row's
status during a modify-in-place sequence.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.fixtures.e2e_chain import chain_client as chain_client
from tests.fixtures.sidecar_servicer import FakeBrokerServicer


@pytest.mark.asyncio
async def test_full_modify_chain(
    chain_client: tuple[AsyncClient, FakeBrokerServicer],
) -> None:
    client, _servicer = chain_client
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
    # 201 = first time this run; 409 = previous run left the row (which
    # may be in either True or False state). PUT after to force-set True.
    assert r.status_code in (201, 409), r.text
    r = await client.put(
        "/api/admin/config/broker/isa-paper.trade_enabled",
        json={
            "namespace": "broker",
            "key": "isa-paper.trade_enabled",
            "value": True,
            "value_type": "bool",
        },
    )
    assert r.status_code == 200, r.text

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
        json={
            "nonce": modify_nonce,
            "qty": "2",
            "limit_price": "1",
            "order_type": "LIMIT",
            "tif": "DAY",
        },
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

    # Phase 11b: the modify path emits two broker stream events after the
    # row's status reaches 'modified' — a cancelled(kind=replaced) for the
    # old broker_order_id (which OrderEventConsumer now short-circuits)
    # and a submitted(kind=status) for the new broker_order_id. Both run
    # in short consumer transactions that hold a row lock; if DELETE
    # fires while the second one is mid-flight, _locked_order_for_cancel's
    # FOR UPDATE NOWAIT raises LockNotAvailable → 423. A small drain
    # window lets the consumer finish the second event so cancel doesn't
    # race it. Underlying retry-on-lock-conflict is a separate hardening
    # item tracked in the recovery doc.
    await asyncio.sleep(0.2)

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
