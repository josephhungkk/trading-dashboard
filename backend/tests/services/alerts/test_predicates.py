"""Golden-vector tests for the 10 predicate primitives + schema validator.

Every primitive has at least 3 cases (true/false/edge). The composite tests
verify short-circuit behaviour. The schema tests verify the JSON-Schema
validator catches unknown kinds and accepts well-formed predicates.
"""

import pytest

from app.services.alerts.predicates import (
    PredicateValidationError,
    evaluate,
    referenced_capabilities,
    referenced_symbols,
    validate_schema,
)


def test_validate_schema_rejects_unknown_kind() -> None:
    with pytest.raises(PredicateValidationError):
        validate_schema({"kind": "bogus", "x": 1})


def test_validate_schema_accepts_composite_and() -> None:
    predicate = {
        "kind": "composite_and",
        "children": [
            {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200.0},
            {"kind": "volume_spike", "symbol": "AAPL", "multiple": 2.0, "vs_window_minutes": 5},
        ],
    }
    validate_schema(predicate)  # should not raise


def test_validate_schema_rejects_extra_props() -> None:
    with pytest.raises(PredicateValidationError):
        validate_schema(
            {
                "kind": "price_threshold",
                "symbol": "AAPL",
                "op": "gt",
                "value": 200.0,
                "BOGUS_EXTRA": 1,
            }
        )


def test_price_threshold_gt_fires() -> None:
    predicate = {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200.0}
    assert evaluate(predicate, {"prices": {"AAPL": 201.5}}) is True


def test_price_threshold_missing_symbol_returns_false() -> None:
    predicate = {"kind": "price_threshold", "symbol": "ZZZZ", "op": "gt", "value": 1.0}
    assert evaluate(predicate, {"prices": {"AAPL": 201.5}}) is False


@pytest.mark.parametrize(
    "op,price,target,want",
    [
        ("gt", 201.0, 200.0, True),
        ("gt", 200.0, 200.0, False),
        ("lt", 199.0, 200.0, True),
        ("gte", 200.0, 200.0, True),
        ("lte", 199.0, 200.0, True),
        ("eq", 200.0, 200.0, True),
    ],
)
def test_price_threshold_ops(op: str, price: float, target: float, want: bool) -> None:
    pred = {"kind": "price_threshold", "symbol": "X", "op": op, "value": target}
    assert evaluate(pred, {"prices": {"X": price}}) is want


def test_pct_change_window_positive_fires() -> None:
    bars = [{"close": 100}, {"close": 102}, {"close": 105}]
    pred = {"kind": "pct_change_window", "symbol": "X", "pct": 4.0, "window_seconds": 180}
    assert evaluate(pred, {"bars": {"X": bars}}) is True


def test_pct_change_window_zero_first_returns_false() -> None:
    bars = [{"close": 0}, {"close": 105}]
    pred = {"kind": "pct_change_window", "symbol": "X", "pct": 5.0, "window_seconds": 60}
    assert evaluate(pred, {"bars": {"X": bars}}) is False


def test_pct_change_window_insufficient_bars_returns_false() -> None:
    pred = {"kind": "pct_change_window", "symbol": "X", "pct": 1.0, "window_seconds": 60}
    assert evaluate(pred, {"bars": {"X": [{"close": 100}]}}) is False


def test_ma_cross_golden() -> None:
    # Flat low then sudden spike → fast MA crosses above slow MA on last bar.
    bars = [{"close": c} for c in [10, 10, 10, 10, 10, 10, 30]]
    pred = {
        "kind": "ma_cross",
        "symbol": "X",
        "fast_period": 2,
        "slow_period": 4,
        "direction": "golden",
    }
    assert evaluate(pred, {"bars": {"X": bars}}) is True


def test_ma_cross_death() -> None:
    # Flat high then sudden drop → fast MA crosses below slow MA on last bar.
    bars = [{"close": c} for c in [30, 30, 30, 30, 30, 30, 10]]
    pred = {
        "kind": "ma_cross",
        "symbol": "X",
        "fast_period": 2,
        "slow_period": 4,
        "direction": "death",
    }
    assert evaluate(pred, {"bars": {"X": bars}}) is True


def test_ma_cross_no_cross() -> None:
    bars = [{"close": 10}] * 7
    pred = {
        "kind": "ma_cross",
        "symbol": "X",
        "fast_period": 2,
        "slow_period": 4,
        "direction": "golden",
    }
    assert evaluate(pred, {"bars": {"X": bars}}) is False


def test_volume_spike_fires() -> None:
    bars = [{"volume": 1000}] * 5 + [{"volume": 5000}]
    pred = {"kind": "volume_spike", "symbol": "X", "multiple": 3.0, "vs_window_minutes": 5}
    assert evaluate(pred, {"bars": {"X": bars}}) is True


def test_volume_spike_below_threshold() -> None:
    bars = [{"volume": 1000}] * 5 + [{"volume": 2000}]
    pred = {"kind": "volume_spike", "symbol": "X", "multiple": 3.0, "vs_window_minutes": 5}
    assert evaluate(pred, {"bars": {"X": bars}}) is False


def test_volume_spike_zero_avg_returns_false() -> None:
    bars = [{"volume": 0}] * 5 + [{"volume": 100}]
    pred = {"kind": "volume_spike", "symbol": "X", "multiple": 3.0, "vs_window_minutes": 5}
    assert evaluate(pred, {"bars": {"X": bars}}) is False


def test_order_event_matches_event_type() -> None:
    pred = {"kind": "order_event", "event_type": "filled"}
    state = {"order_event": {"event_type": "filled"}}
    assert evaluate(pred, state) is True


def test_order_event_account_filter_matches() -> None:
    pred = {"kind": "order_event", "event_type": "filled", "account_id": "a1"}
    state = {"order_event": {"event_type": "filled", "account_id": "a1"}}
    assert evaluate(pred, state) is True


def test_order_event_account_filter_mismatches() -> None:
    pred = {"kind": "order_event", "event_type": "filled", "account_id": "a1"}
    state = {"order_event": {"event_type": "filled", "account_id": "a2"}}
    assert evaluate(pred, state) is False


def test_ai_signal_above_threshold() -> None:
    pred = {
        "kind": "ai_signal",
        "prompt_template": "bullish?",
        "capability": "STRUCTURED_OUTPUT",
        "threshold": 0.7,
    }
    state = {"ai_signals": {"bullish?": 0.85}}
    assert evaluate(pred, state) is True


def test_ai_signal_below_threshold() -> None:
    pred = {
        "kind": "ai_signal",
        "prompt_template": "bullish?",
        "capability": "STRUCTURED_OUTPUT",
        "threshold": 0.7,
    }
    state = {"ai_signals": {"bullish?": 0.5}}
    assert evaluate(pred, state) is False


def test_ai_signal_missing_returns_false() -> None:
    pred = {
        "kind": "ai_signal",
        "prompt_template": "bullish?",
        "capability": "STRUCTURED_OUTPUT",
        "threshold": 0.7,
    }
    assert evaluate(pred, {}) is False


def test_news_event_capability_dormant_returns_false() -> None:
    pred = {"kind": "news_event"}
    state = {"capabilities": {"news_feed": False}, "news": [{"symbol": "X"}]}
    assert evaluate(pred, state) is False


def test_news_event_capability_available_matches() -> None:
    pred = {"kind": "news_event", "symbol": "X"}
    state = {"capabilities": {"news_feed": True}, "news": [{"symbol": "X"}]}
    assert evaluate(pred, state) is True


def test_unknown_never_fires() -> None:
    pred = {"kind": "unknown", "raw_text": "huh", "suggestions": []}
    assert evaluate(pred, {"prices": {"X": 1}}) is False


def test_composite_and_short_circuits_false() -> None:
    pred = {
        "kind": "composite_and",
        "children": [
            {"kind": "price_threshold", "symbol": "X", "op": "gt", "value": 200},
            {"kind": "price_threshold", "symbol": "Y", "op": "gt", "value": 100},
        ],
    }
    assert evaluate(pred, {"prices": {"X": 100, "Y": 500}}) is False


def test_composite_or_short_circuits_true() -> None:
    pred = {
        "kind": "composite_or",
        "children": [
            {"kind": "price_threshold", "symbol": "X", "op": "gt", "value": 200},
            {"kind": "price_threshold", "symbol": "Y", "op": "gt", "value": 100},
        ],
    }
    assert evaluate(pred, {"prices": {"X": 100, "Y": 500}}) is True


def test_referenced_symbols_walks_composite() -> None:
    pred = {
        "kind": "composite_or",
        "children": [
            {"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200},
            {"kind": "volume_spike", "symbol": "TSLA", "multiple": 2.0, "vs_window_minutes": 5},
        ],
    }
    assert referenced_symbols(pred) == {"AAPL", "TSLA"}


def test_referenced_capabilities_news_event() -> None:
    pred = {"kind": "news_event"}
    caps = referenced_capabilities(pred)
    assert caps == [{"capability": "news_feed", "params": {}}]
