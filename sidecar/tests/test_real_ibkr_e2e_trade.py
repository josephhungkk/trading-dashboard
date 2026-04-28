"""Real paper IBKR trade chain (5b.1 D3-real). @pytest.mark.real_ibkr gated.

Runs against paper gateway 4002 nightly via nightly-real-ibkr.yml + manual
dispatch. Pre-flight asserts maintenance window not active. Idempotent via
UUIDv7 client_order_id dedup. Cleanup in finally block: revert flag.
"""

from __future__ import annotations

import os
import time as _t
import uuid

import httpx
import pytest

CF_BASE = "https://dashboard.kiusinghung.com"


def _headers() -> dict[str, str]:
    return {
        "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        "Content-Type": "application/json",
    }


@pytest.mark.real_ibkr
def test_real_paper_trade_chain() -> None:
    """7-step chain against real paper gateway 4002."""
    # 0. Pre-flight maintenance check
    r = httpx.get(f"{CF_BASE}/api/accounts", headers=_headers())
    assert r.status_code == 200
    assert r.json()["broker_maintenance"]["active"] is False

    paper = [a for a in r.json()["accounts"] if a.get("mode") == "paper"]
    assert paper, "no paper accounts in prod"
    acct_id = paper[0]["id"]

    try:
        # 1. Enable trade_enabled (idempotent: 201 fresh, 409 already-set)
        r = httpx.post(
            f"{CF_BASE}/api/admin/config",
            headers=_headers(),
            json={
                "namespace": "broker",
                "key": "isa-paper.trade_enabled",
                "value": True,
                "value_type": "bool",
            },
        )
        assert r.status_code in (201, 409)

        # 2. Preview (BARC GBP - works for GBP-base accounts; AAPL would
        #    require USD:GBP fx rate cached)
        r = httpx.post(
            f"{CF_BASE}/api/orders/preview",
            headers=_headers(),
            json={
                "account_id": acct_id,
                "conid": "908940",
                "side": "BUY",
                "order_type": "LIMIT",
                "tif": "DAY",
                "qty": "1",
                "limit_price": "1",
            },
        )
        assert r.status_code == 200, f"preview failed: {r.text}"
        prev = r.json()

        # 3. Place
        coid = str(uuid.uuid4())
        r = httpx.post(
            f"{CF_BASE}/api/orders",
            headers=_headers(),
            json={
                "account_id": acct_id,
                "client_order_id": coid,
                "conid": "908940",
                "side": "BUY",
                "order_type": "LIMIT",
                "tif": "DAY",
                "qty": "1",
                "limit_price": "1",
                "nonce": prev["nonce"],
            },
        )
        assert r.status_code == 200, f"place failed: {r.text}"
        order_id = r.json()["id"]

        # 4. Cancel
        r = httpx.delete(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
        assert r.status_code == 202

        # 5. Verify SIM cancel echo flowed through (within 5s)
        deadline = _t.time() + 5.0
        while _t.time() < deadline:
            r = httpx.get(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
            if r.json()["status"] == "cancelled":
                break
            _t.sleep(0.5)
        assert r.json()["status"] == "cancelled", f"final: {r.json()}"

    finally:
        # Always revert trade_enabled, even on test failure
        httpx.put(
            f"{CF_BASE}/api/admin/config/broker/isa-paper.trade_enabled",
            headers=_headers(),
            json={
                "namespace": "broker",
                "key": "isa-paper.trade_enabled",
                "value": False,
                "value_type": "bool",
            },
        )
