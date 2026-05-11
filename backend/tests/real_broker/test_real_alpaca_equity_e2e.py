"""Phase 8c T-S.9 - Real Alpaca paper-account equity E2E place + cancel.

Runs against live Alpaca paper API. Auto-skipped when ALPACA_PAPER_* env vars
are not set (see conftest.py). Invoked by
.github/workflows/nightly-real-alpaca-equity.yml.

Cases (passed via --case):
  market_spy - MARKET BUY 1 SPY -> poll -> cancel
  limit_spy  - LIMIT BUY 1 SPY @ $1.00, DAY -> cancel
  trail_spy  - TRAILING_STOP BUY 1 SPY with 2% trail -> cancel
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.real_alpaca_equity

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.environ["ALPACA_PAPER_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_API_SECRET"],
        "Content-Type": "application/json",
    }


def _build_market_spy_payload() -> dict[str, Any]:
    return {
        "symbol": "SPY",
        "qty": "1",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }


def _build_limit_spy_payload() -> dict[str, Any]:
    return {
        "symbol": "SPY",
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.00",
    }


def _build_trail_spy_payload() -> dict[str, Any]:
    return {
        "symbol": "SPY",
        "qty": "1",
        "side": "buy",
        "type": "trailing_stop",
        "time_in_force": "day",
        "trail_percent": "2.0",
    }


def _place(payload: dict[str, Any]) -> str:
    response = httpx.post(
        f"{ALPACA_PAPER_BASE_URL}/orders",
        headers=_headers(),
        json=payload,
        timeout=20.0,
    )
    assert response.status_code in (200, 201), (
        f"place failed: {response.status_code} {response.text}"
    )
    order_id = response.json().get("id")
    assert order_id, f"missing id in place response: {response.json()}"
    return str(order_id)


def _cancel(order_id: str) -> None:
    response = httpx.delete(
        f"{ALPACA_PAPER_BASE_URL}/orders/{order_id}",
        headers=_headers(),
        timeout=20.0,
    )
    # 200/204 = cancel accepted. 422 with "already in filled/canceled/expired
    # state" = the order reached a terminal state before our cancel arrived
    # (market orders fill near-instantly on alpaca paper during market hours),
    # which is functionally indistinguishable from a successful cancel for
    # this lifecycle smoke test. Any other 422 (e.g. invalid order id) still
    # fails.
    if response.status_code in (200, 204):
        return
    if response.status_code == 422 and any(
        marker in response.text for marker in ("filled", "canceled", "expired")
    ):
        return
    raise AssertionError(f"cancel failed: {response.status_code} {response.text}")


def _poll_terminal_or_cancelled(order_id: str, timeout: float = 8.0) -> str:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{ALPACA_PAPER_BASE_URL}/orders/{order_id}",
            headers=_headers(),
            timeout=20.0,
        )
        assert response.status_code == 200, (
            f"order details failed: {response.status_code} {response.text}"
        )
        last_status = response.json().get("status")
        if last_status in {"canceled", "expired", "filled", "rejected"}:
            return str(last_status)
        time.sleep(0.5)
    assert last_status in {"canceled", "pending_cancel"}, f"unexpected order status: {last_status}"
    return str(last_status)


def _payload_for_case(case_name: str) -> dict[str, Any]:
    if case_name == "market_spy":
        return _build_market_spy_payload()
    if case_name == "limit_spy":
        return _build_limit_spy_payload()
    if case_name == "trail_spy":
        return _build_trail_spy_payload()
    raise AssertionError(f"unknown Alpaca equity E2E case: {case_name}")


def test_real_alpaca_equity_place_cancel(case: str) -> None:
    order_id = _place(_payload_for_case(case))
    try:
        time.sleep(2)
        _cancel(order_id)
        status = _poll_terminal_or_cancelled(order_id)
        assert status in {"canceled", "pending_cancel", "filled"}
    except Exception as exc:
        try:
            _cancel(order_id)
        except Exception:
            pass
        raise exc
