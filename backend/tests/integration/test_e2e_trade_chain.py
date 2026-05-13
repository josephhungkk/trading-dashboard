"""End-to-end trade chain test (5b.1 D1).

Drives the full preview -> place -> cancel chain through the FastAPI
ASGITransport (no real ports) against the extended sidecar mock servicer.
Assertions catch all five v0.5.1 bugs deterministically.

Phase 11a CI-debt sweep (2026-05-12): unskipped after the
``e2e_chain.chain_client`` fixture landed (commit 59d4c08) and the two
real risk-gate bugs it surfaced were fixed (commit e7e9fa0).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.fixtures.e2e_chain import chain_client as chain_client
from tests.fixtures.sidecar_servicer import FakeBrokerServicer


@pytest.mark.asyncio
async def test_full_trade_chain(
    chain_client: tuple[AsyncClient, FakeBrokerServicer],
) -> None:
    client, _servicer = chain_client
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
    # 201 first time, 409 if prior test/run left the row. Either way the
    # state we want (enabled=True) is in place; the modify chain test
    # uses the same pattern.
    assert r.status_code in (201, 409), r.text

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

    # Phase 11b: give the OrderEventConsumer a moment to drain the
    # FakeBrokerServicer's `submitted` stream event before DELETE locks
    # the row. Without this, _locked_order_for_cancel's FOR UPDATE NOWAIT
    # races the consumer's _update_order and raises LockNotAvailable.
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
