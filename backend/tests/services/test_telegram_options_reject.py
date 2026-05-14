"""OCC option symbols must be rejected by parse_place_order."""

from __future__ import annotations


def test_occ_symbol_5char_rejected():
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order SPY250117C00450000 BUY 1")
    assert result is None


def test_occ_symbol_4char_rejected():
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order AAPL250117P00180000 BUY 1")
    assert result is None


def test_occ_symbol_6char_rejected():
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order BRKBSR250117P00400000 BUY 1")
    assert result is None


def test_equity_symbol_allowed():
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order AAPL BUY 10 --limit 150.00")
    assert result is not None
    assert result.symbol == "AAPL"


def test_equity_3char_allowed():
    from app.services.telegram.order_flow import parse_place_order

    result = parse_place_order("/place_order SPY BUY 5")
    assert result is not None
