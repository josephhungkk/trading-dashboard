"""Phase 8a E4 — Real Schwab paper-account E2E place + cancel.

Runs against live Schwab sandbox API. Auto-skipped when SCHWAB_* env vars
are not set (see conftest.py). Invoked by .github/workflows/nightly-real-schwab.yml.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.real_schwab


def test_real_schwab_place_then_cancel() -> None:
    import schwabdev

    app_key = os.environ["SCHWAB_APP_KEY"]
    app_secret = os.environ["SCHWAB_APP_SECRET"]
    acct_hash = os.environ["SCHWAB_PAPER_ACCOUNT_HASH"]
    symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "F")

    client = schwabdev.Client(app_key, app_secret, tokens_db="/tmp/nightly_tokens.db")
    client_order_id = f"NIGHTLY-{int(time.time())}"
    payload = {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": "1.00",
        "clientOrderId": client_order_id,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }
    place = client.order_place(acct_hash, payload)
    assert place.status_code in (200, 201), f"place failed: {place.status_code} {place.text}"
    assert "Location" in place.headers, "missing Location header on place response"
    broker_order_id = place.headers["Location"].rsplit("/", 1)[-1]

    time.sleep(2)
    cancel = client.order_cancel(acct_hash, broker_order_id)
    assert cancel.status_code in (200, 204), f"cancel failed: {cancel.status_code}"

    time.sleep(2)
    detail = client.order_details(acct_hash, broker_order_id).json()
    assert detail.get("status") in {"CANCELED", "PENDING_CANCEL"}, detail.get("status")
