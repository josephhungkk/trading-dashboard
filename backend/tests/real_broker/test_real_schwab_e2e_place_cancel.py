"""Phase 8a E4 / Phase 8b T-S.4 — Real Schwab paper-account E2E place + cancel.

Runs against live Schwab sandbox API. Auto-skipped when SCHWAB_* env vars
are not set (see conftest.py). Invoked by .github/workflows/nightly-real-schwab-trade.yml.

Cases (passed via --case):
  market_spy       — DAY LIMIT $1.00 on SPY (original Phase 8a case)
  trail_amount_spy — TRAIL $0.10 amount on SPY
  gtd_limit_spy    — GTD LIMIT $1.00 on SPY expiring tomorrow
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.real_schwab


def _extract_order_id(place_response: object) -> str:
    """Pull broker_order_id from the Location header of a place response."""
    location: str = place_response.headers["Location"]  # type: ignore[union-attr]
    return location.rsplit("/", 1)[-1]


def _place_and_cancel(client: object, acct_hash: str, payload: dict) -> None:  # type: ignore[type-arg]
    """Place an order, wait briefly, cancel it, then verify cancellation."""
    place = client.order_place(acct_hash, payload)  # type: ignore[union-attr]
    assert place.status_code in (200, 201), f"place failed: {place.status_code} {place.text}"
    assert "Location" in place.headers, "missing Location header on place response"

    broker_order_id = _extract_order_id(place)

    time.sleep(2)
    cancel = client.order_cancel(acct_hash, broker_order_id)  # type: ignore[union-attr]
    assert cancel.status_code in (200, 204), f"cancel failed: {cancel.status_code}"

    time.sleep(2)
    detail = client.order_details(acct_hash, broker_order_id).json()  # type: ignore[union-attr]
    assert detail.get("status") in {"CANCELED", "PENDING_CANCEL"}, detail.get("status")


def _build_market_spy_payload(symbol: str, client_order_id: str) -> dict:  # type: ignore[type-arg]
    """DAY LIMIT $1.00 — deeply unfillable, original Phase 8a scenario."""
    return {
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


def _build_trail_amount_spy_payload(symbol: str, client_order_id: str) -> dict:  # type: ignore[type-arg]
    """TRAIL order with $0.10 amount offset — never fills far from market."""
    return {
        "orderType": "TRAILING_STOP",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "stopPriceLinkBasis": "LAST",
        "stopPriceLinkType": "VALUE",
        "stopPriceOffset": "0.10",
        "clientOrderId": client_order_id,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }


def _build_gtd_limit_spy_payload(symbol: str, client_order_id: str) -> dict:  # type: ignore[type-arg]
    """GTD LIMIT $1.00 expiring tomorrow — deeply unfillable."""
    expiry_date: str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "GOOD_TILL_CANCEL",
        "orderStrategyType": "SINGLE",
        "price": "1.00",
        "cancelTime": expiry_date,
        "clientOrderId": client_order_id,
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 1,
                "instrument": {"symbol": symbol, "assetType": "EQUITY"},
            }
        ],
    }


def test_real_schwab_place_then_cancel(case: str) -> None:
    import schwabdev

    app_key = os.environ["SCHWAB_APP_KEY"]
    app_secret = os.environ["SCHWAB_APP_SECRET"]
    acct_hash = os.environ["SCHWAB_PAPER_ACCOUNT_HASH"]
    symbol = os.environ.get("SCHWAB_PAPER_SYMBOL", "SPY")

    # Phase 10a.5.1: token DB path is now configurable per matrix case so
    # parallel runs don't share a SQLite file (concurrent refresh on a
    # single file produced "database is locked" + occasional refresh-token
    # revocation by Schwab when 3 jobs raced for the same refresh).
    # The CI seeds this path from SCHWAB_TOKENS_DB_B64; absent that, skip
    # rather than blocking on schwabdev's interactive OAuth stdin prompt.
    tokens_db = os.environ.get("SCHWAB_TOKENS_DB", "/tmp/nightly_tokens.db")
    if not os.path.exists(tokens_db):
        pytest.skip(
            f"Schwab tokens DB at {tokens_db} not seeded; set "
            "SCHWAB_TOKENS_DB_B64 secret in CI or pre-seed locally via "
            "schwabdev's interactive auth flow."
        )
    client = schwabdev.Client(app_key, app_secret, tokens_db=tokens_db)
    client_order_id = f"NIGHTLY-{case}-{int(time.time())}"

    if case == "market_spy":
        payload = _build_market_spy_payload(symbol, client_order_id)
    elif case == "trail_amount_spy":
        payload = _build_trail_amount_spy_payload(symbol, client_order_id)
    elif case == "gtd_limit_spy":
        payload = _build_gtd_limit_spy_payload(symbol, client_order_id)
    else:
        pytest.fail(f"Unknown --case value: {case!r}")

    _place_and_cancel(client, acct_hash, payload)
