from decimal import Decimal

import pytest

from app.services.algo.schemas import (
    ALGO_PARAM_SCHEMAS,
    AlgoStrategy,
    _normalize_algo_params,
)

pytestmark = pytest.mark.no_db


def test_algo_strategy_members():
    assert set(AlgoStrategy) == {
        "ADAPTIVE",
        "TWAP",
        "VWAP",
        "ARRIVAL_PRICE",
        "ICEBERG",
        "RESERVE",
        "DARK_ICE",
    }


def test_normalize_bool():
    assert _normalize_algo_params({"allow_past_end_time": True}) == {"allow_past_end_time": "true"}
    assert _normalize_algo_params({"flag": False}) == {"flag": "false"}


def test_normalize_int():
    assert _normalize_algo_params({"max_pct_vol": 15}) == {"max_pct_vol": "15"}


def test_normalize_decimal():
    assert _normalize_algo_params({"display_size": Decimal("50.5")}) == {"display_size": "50.5"}


def test_normalize_str_passthrough():
    assert _normalize_algo_params({"urgency": "NORMAL"}) == {"urgency": "NORMAL"}


def test_normalize_invalid_list_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": [1, 2]})


def test_normalize_invalid_dict_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": {"nested": "dict"}})


def test_normalize_none_value_raises():
    with pytest.raises(ValueError, match="unsupported type"):
        _normalize_algo_params({"bad": None})


def test_algo_param_schemas_has_all_strategies():
    for strategy in AlgoStrategy:
        assert strategy in ALGO_PARAM_SCHEMAS, f"Missing schema for {strategy}"


def test_algo_param_schemas_required_fields():
    adaptive = ALGO_PARAM_SCHEMAS["ADAPTIVE"]
    urgency = next(p for p in adaptive if p["name"] == "urgency")
    assert urgency["required"] is True
    assert urgency["type"] == "enum"
    assert "PATIENT" in urgency["values"]
