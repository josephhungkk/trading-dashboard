import pytest
from sidecar_schwab.normalize import to_schwab_oco_payload


def _leg(order_type="LIMIT", price="10.00", side="BUY", symbol="SPY"):
    return {
        "order_type": order_type,
        "tif": "DAY",
        "limit_price": price,
        "side": side,
        "symbol": symbol,
        "qty": "1",
    }


def test_oco_payload_has_orderStrategyType_OCO():
    p = to_schwab_oco_payload(_leg(price="9.00"), _leg(price="11.00"))
    assert p["orderStrategyType"] == "OCO"


def test_oco_payload_has_two_children():
    p = to_schwab_oco_payload(_leg(price="9.00"), _leg(price="11.00"))
    assert len(p["childOrderStrategies"]) == 2


def test_oco_payload_each_child_has_single_strategy():
    p = to_schwab_oco_payload(_leg(price="9.00"), _leg(price="11.00"))
    for child in p["childOrderStrategies"]:
        # Each child is a complete SINGLE order (orderType/duration/legs)
        assert child["orderType"] in {"LIMIT", "MARKET", "STOP", "STOP_LIMIT"}
        assert "orderLegCollection" in child


def test_oco_legs_symbol_mismatch_rejected():
    with pytest.raises(ValueError, match="oco_legs_symbol_mismatch"):
        to_schwab_oco_payload(_leg(symbol="SPY"), _leg(symbol="QQQ"))
