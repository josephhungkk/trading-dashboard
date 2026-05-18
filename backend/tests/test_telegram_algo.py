"""Tests for Telegram algo order parsing."""

import pytest

from app.services.telegram.order_flow import parse_place_order

pytestmark = pytest.mark.no_db


def test_parse_adaptive():
    result = parse_place_order("/place_order AAPL BUY 100 ADAPTIVE urgency=URGENT")
    assert result is not None
    assert result.algo_strategy == "ADAPTIVE"
    assert result.algo_params == {"urgency": "URGENT"}


def test_parse_twap():
    result = parse_place_order("/place_order AAPL BUY 1000 TWAP start_time=10:00 end_time=14:00")
    assert result is not None
    assert result.algo_strategy == "TWAP"
    assert result.algo_params["start_time"] == "10:00"
    assert result.algo_params["end_time"] == "14:00"


def test_parse_vwap_with_optional():
    result = parse_place_order(
        "/place_order AAPL BUY 1000 VWAP start_time=10:00 end_time=14:00 max_pct_vol=15"
    )
    assert result is not None
    assert result.algo_strategy == "VWAP"
    assert result.algo_params["max_pct_vol"] == "15"


def test_parse_arrival_price():
    result = parse_place_order("/place_order AAPL BUY 500 ARRIVAL_PRICE urgency=NORMAL")
    assert result is not None
    assert result.algo_strategy == "ARRIVAL_PRICE"


def test_parse_iceberg():
    result = parse_place_order("/place_order AAPL BUY 500 ICEBERG display_size=50")
    assert result is not None
    assert result.algo_strategy == "ICEBERG"
    assert result.algo_params["display_size"] == "50"


def test_parse_reserve():
    result = parse_place_order(
        "/place_order AAPL BUY 500 RESERVE display_size=50 randomize_size=true"
    )
    assert result is not None
    assert result.algo_strategy == "RESERVE"
    assert result.algo_params["randomize_size"] == "true"


def test_parse_dark_ice():
    result = parse_place_order("/place_order AAPL BUY 500 DARK_ICE display_size=50")
    assert result is not None
    assert result.algo_strategy == "DARK_ICE"


def test_dark_ice_display_size_zero_rejected():
    result = parse_place_order("/place_order AAPL BUY 500 DARK_ICE display_size=0")
    assert result is None  # parse-time validation rejects it


def test_unknown_key_rejected():
    result = parse_place_order("/place_order AAPL BUY 100 ADAPTIVE bad_key=x")
    assert result is None


def test_case_insensitive_strategy():
    result = parse_place_order("/place_order AAPL BUY 100 adaptive urgency=URGENT")
    assert result is not None
    assert result.algo_strategy == "ADAPTIVE"


def test_non_algo_path_unchanged():
    result = parse_place_order("/place_order AAPL BUY 100 --limit 150.00")
    assert result is not None
    assert result.algo_strategy is None
    assert result.limit_price == "150.00"
