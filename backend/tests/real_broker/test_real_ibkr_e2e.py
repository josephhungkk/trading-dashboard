"""Phase 8b T-I.4 — Real IBKR paper-account E2E: TRAIL/MOC/GTD scenarios.

Runs against the live deployed dashboard CF endpoint. Auto-skipped when
IBKR_PAPER_ACCOUNT or CF_ACCESS_* env vars are missing (see conftest.py).
Invoked by .github/workflows/nightly-real-ibkr.yml (post-NYSE-close).

Cases (passed via --case):
  market_spy        — MKT BUY 1 SPY → poll → cancel
  trail_percent_spy — TRAIL BUY 1 SPY trailingPercent=2.0 → poll → cancel
  moc_spy           — MOC BUY 1 SPY (DAY-only); skipped outside MOC window
  gtd_limit_spy     — LIMIT BUY 1 SPY @ $1.00, tif=GTD, expiry=tomorrow → cancel
"""

from __future__ import annotations

import datetime
import os
import time

import pytest

# httpx is a backend dependency; skip the whole module if not importable.
httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.real_ibkr

CF_BASE = "https://dashboard.kiusinghung.com"

# SPY conid (IEX SMART routing) — stable permanent IBKR identifier.
_SPY_CONID = "756733"


def _headers() -> dict[str, str]:
    return {
        "CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
        "Content-Type": "application/json",
    }


def _account_id() -> str:
    return os.environ["IBKR_PAPER_ACCOUNT"]


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _build_preview_payload(account_id: str, order_type: str, tif: str, **extra: object) -> dict:  # type: ignore[type-arg]
    base: dict = {  # type: ignore[type-arg]
        "account_id": account_id,
        "conid": _SPY_CONID,
        "side": "BUY",
        "order_type": order_type,
        "tif": tif,
        "qty": "1.00000000",
    }
    base.update(extra)
    return base


def _build_market_payload(account_id: str, nonce: str) -> dict:  # type: ignore[type-arg]
    return {
        "account_id": account_id,
        "conid": _SPY_CONID,
        "side": "BUY",
        "order_type": "MKT",
        "tif": "DAY",
        "qty": "1.00000000",
        "nonce": nonce,
    }


def _build_trail_payload(account_id: str, nonce: str) -> dict:  # type: ignore[type-arg]
    """TRAIL order with 2% trailing percent — fills only if price drops 2%."""
    return {
        "account_id": account_id,
        "conid": _SPY_CONID,
        "side": "BUY",
        "order_type": "TRAIL",
        "tif": "DAY",
        "qty": "1.00000000",
        "trail_offset": "2.00000000",
        "trail_offset_type": "PERCENT",
        "nonce": nonce,
    }


def _build_moc_payload(account_id: str, nonce: str) -> dict:  # type: ignore[type-arg]
    """MOC BUY 1 SPY — fills at closing auction; DAY-only."""
    return {
        "account_id": account_id,
        "conid": _SPY_CONID,
        "side": "BUY",
        "order_type": "MOC",
        "tif": "DAY",
        "qty": "1.00000000",
        "nonce": nonce,
    }


def _build_gtd_payload(account_id: str, nonce: str) -> dict:  # type: ignore[type-arg]
    """LIMIT BUY 1 SPY @ $1.00 (deeply unfillable), GTD expiring tomorrow."""
    expiry_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "account_id": account_id,
        "conid": _SPY_CONID,
        "side": "BUY",
        "order_type": "LIMIT",
        "tif": "GTD",
        "qty": "1.00000000",
        "limit_price": "1.00000000",
        "expiry_date": expiry_date,
        "nonce": nonce,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_trade(account_alias: str = "isa-paper") -> None:
    """Enable trade_enabled config for the paper gateway (idempotent)."""
    r = httpx.post(
        f"{CF_BASE}/api/admin/config",
        headers=_headers(),
        json={
            "namespace": "broker",
            "key": f"{account_alias}.trade_enabled",
            "value": True,
            "value_type": "bool",
        },
    )
    assert r.status_code in (201, 409), f"enable trade_enabled failed: {r.status_code} {r.text}"


def _disable_trade(account_alias: str = "isa-paper") -> None:
    """Revert trade_enabled (best-effort, called from finally)."""
    try:
        httpx.put(
            f"{CF_BASE}/api/admin/config/broker/{account_alias}.trade_enabled",
            headers=_headers(),
            json={
                "namespace": "broker",
                "key": f"{account_alias}.trade_enabled",
                "value": False,
                "value_type": "bool",
            },
        )
    except Exception:
        pass


def _preview(preview_payload: dict) -> str:  # type: ignore[type-arg]
    """POST /api/orders/preview and return the nonce."""
    r = httpx.post(f"{CF_BASE}/api/orders/preview", headers=_headers(), json=preview_payload)
    assert r.status_code == 200, f"preview failed: {r.status_code} {r.text}"
    nonce = r.json().get("nonce")
    assert nonce, f"missing nonce in preview response: {r.json()}"
    return str(nonce)


def _place(place_payload: dict) -> str:  # type: ignore[type-arg]
    """POST /api/orders and return internal order UUID."""
    r = httpx.post(f"{CF_BASE}/api/orders", headers=_headers(), json=place_payload)
    assert r.status_code == 200, f"place failed: {r.status_code} {r.text}"
    order_id = r.json().get("id")
    assert order_id, f"missing id in place response: {r.json()}"
    return str(order_id)


def _cancel(order_id: str) -> None:
    """DELETE /api/orders/{order_id} and assert 202."""
    r = httpx.delete(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
    assert r.status_code == 202, f"cancel failed: {r.status_code} {r.text}"


def _poll_cancelled(order_id: str, timeout: float = 8.0) -> None:
    """Poll GET /api/orders/{order_id} until status is cancelled or PENDING_CANCEL."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        r = httpx.get(f"{CF_BASE}/api/orders/{order_id}", headers=_headers())
        if r.status_code == 200:
            status = r.json().get("status")
            if status in {"cancelled", "PENDING_CANCEL", "CANCELED"}:
                return
        time.sleep(0.5)
    raise AssertionError(f"order {order_id} not cancelled within {timeout}s; last status={status}")


def _is_moc_window_open() -> bool:
    """Return True if current UTC time is within approx NYSE MOC window.

    NYSE MOC cutoff is 15:50 ET (= ~19:50 UTC during EDT, ~20:50 UTC during EST).
    We skip conservatively: allow placement only between 15:00 UTC and 19:50 UTC
    (covers EDT; EST window would be tighter but we target EDT/summer schedule).
    """
    now_utc = datetime.datetime.utcnow()
    return 15 <= now_utc.hour < 20


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


def test_real_ibkr_place_then_cancel(case: str) -> None:
    """Dispatch to per-case scenario and run place → [poll] → cancel chain."""
    account_id = _account_id()

    if case == "moc_spy":
        if not _is_moc_window_open():
            pytest.skip("outside MOC window (approx NYSE MOC cutoff 15:00-19:50 UTC)")

    try:
        _enable_trade()

        if case == "market_spy":
            preview_payload = _build_preview_payload(account_id, "MKT", "DAY")
            nonce = _preview(preview_payload)
            place_payload = _build_market_payload(account_id, nonce)

        elif case == "trail_percent_spy":
            preview_payload = _build_preview_payload(
                account_id,
                "TRAIL",
                "DAY",
                trail_offset="2.00000000",
                trail_offset_type="PERCENT",
            )
            nonce = _preview(preview_payload)
            place_payload = _build_trail_payload(account_id, nonce)

        elif case == "moc_spy":
            preview_payload = _build_preview_payload(account_id, "MOC", "DAY")
            nonce = _preview(preview_payload)
            place_payload = _build_moc_payload(account_id, nonce)

        elif case == "gtd_limit_spy":
            expiry_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            preview_payload = _build_preview_payload(
                account_id,
                "LIMIT",
                "GTD",
                limit_price="1.00000000",
                expiry_date=expiry_date,
            )
            nonce = _preview(preview_payload)
            place_payload = _build_gtd_payload(account_id, nonce)

        else:
            pytest.fail(f"Unknown --case value: {case!r}")

        order_id = _place(place_payload)
        time.sleep(2)
        _cancel(order_id)
        _poll_cancelled(order_id)

    finally:
        _disable_trade()
