"""Phase 8b T-I.2 — constructor mock tests for extended order-type handling.

Imports from sidecar_ibkr.order_builder (no proto/gRPC deps) so the test
suite runs without a live broker sidecar or generated-code sys.modules.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from sidecar_ibkr.order_builder import build_ib_order

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def req(**kwargs: object) -> MagicMock:
    """Build a PlaceOrderRequest mock with sensible defaults; override via kwargs."""
    base: dict[str, object] = {
        "order_type": "MARKET",
        "tif": "DAY",
        "limit_price": "0",
        "stop_price": "0",
        "trail_offset": "0",
        "trail_offset_type": "AMOUNT",
        "trail_limit_offset": "0",
        "expiry_date": "",
        "qty": "100",
        "side": "BUY",
        "symbol": "SPY",
    }
    base.update(kwargs)
    m = MagicMock()
    for k, v in base.items():
        setattr(m, k, v)
    return m


def _build(**kwargs: object) -> object:
    r = req(**kwargs)
    side = "BUY" if r.side == "BUY" else "SELL"
    return build_ib_order(r, side, float(r.qty))


# ---------------------------------------------------------------------------
# Order type → orderType string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("order_type,expected", [
    ("MARKET",      "MKT"),
    ("LIMIT",       "LMT"),
    ("STOP",        "STP"),
    ("STOP_LIMIT",  "STP LMT"),
    ("TRAIL",       "TRAIL"),
    ("TRAIL_LIMIT", "TRAIL LIMIT"),  # TWS API canonical — space between words
    ("MOC",         "MOC"),
    ("MOO",         "MKT"),
    ("LOC",         "LOC"),
    ("LOO",         "LMT"),
])
def test_order_type_strings(order_type: str, expected: str) -> None:
    o = _build(order_type=order_type, limit_price="10.00", stop_price="9.50")
    assert o.orderType == expected


# ---------------------------------------------------------------------------
# MOO / LOO OPG tif
# ---------------------------------------------------------------------------

def test_moo_uses_opg_tif() -> None:
    assert _build(order_type="MOO").tif == "OPG"


def test_loo_uses_opg_tif() -> None:
    assert _build(order_type="LOO", limit_price="10.00").tif == "OPG"


# ---------------------------------------------------------------------------
# TRAIL field population
# ---------------------------------------------------------------------------

def test_trail_amount_sets_aux_price() -> None:
    o = _build(order_type="TRAIL", trail_offset="0.10", trail_offset_type="AMOUNT")
    assert o.auxPrice == pytest.approx(0.10)


def test_trail_percent_sets_trailing_percent() -> None:
    o = _build(order_type="TRAIL", trail_offset="2.5", trail_offset_type="PERCENT")
    assert o.trailingPercent == pytest.approx(2.5)


def test_trail_limit_sets_lmt_and_aux() -> None:
    o = _build(
        order_type="TRAIL_LIMIT",
        trail_offset="0.10",
        trail_offset_type="AMOUNT",
        trail_limit_offset="9.95",
    )
    assert o.auxPrice == pytest.approx(0.10)
    assert o.lmtPrice == pytest.approx(9.95)


# ---------------------------------------------------------------------------
# GTD format
# ---------------------------------------------------------------------------

def test_gtd_format() -> None:
    o = _build(order_type="LIMIT", tif="GTD", expiry_date="2026-05-07", limit_price="10.00")
    assert o.tif == "GTD"
    assert re.match(r"\d{8} 23:59:59 US/Eastern", o.goodTillDate)
