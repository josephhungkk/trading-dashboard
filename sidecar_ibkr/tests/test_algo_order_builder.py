"""Tests for build_ib_algo_order()."""

import pytest
from unittest.mock import MagicMock

from sidecar_ibkr.order_builder import (
    _ALGO_STRATEGY_MAP,
    _ALGO_STRATEGY_MAP_REVERSE,
    build_ib_algo_order,
)


def _make_order():
    order = MagicMock()
    order.algoStrategy = ""
    order.algoParams = []
    order.orderType = "MKT"
    return order


def _make_request(strategy, params, order_type="MARKET"):
    req = MagicMock()
    req.algo_strategy = strategy
    req.algo_params = params
    req.order_type = order_type
    return req


def test_adaptive_sets_algo_strategy():
    order = _make_order()
    request = _make_request("ADAPTIVE", {"urgency": "URGENT"})
    build_ib_algo_order(order, request)
    assert order.algoStrategy == _ALGO_STRATEGY_MAP["ADAPTIVE"]
    tag_keys = {tv.tag for tv in order.algoParams}
    assert "adaptPriority" in tag_keys


def test_twap_sets_start_end_time():
    order = _make_order()
    request = _make_request("TWAP", {"start_time": "10:00", "end_time": "14:00"})
    build_ib_algo_order(order, request)
    tag_map = {tv.tag: tv.value for tv in order.algoParams}
    assert "startTime" in tag_map
    assert "endTime" in tag_map


def test_iceberg_requires_limit():
    order = _make_order()
    order.orderType = "MKT"
    request = _make_request("ICEBERG", {"display_size": "50"}, order_type="MARKET")
    with pytest.raises(ValueError, match="requires LMT"):
        build_ib_algo_order(order, request)


def test_dark_ice_display_size_zero_raises():
    order = _make_order()
    order.orderType = "LMT"
    request = _make_request("DARK_ICE", {"display_size": "0"}, order_type="LIMIT")
    with pytest.raises(ValueError, match="display_size"):
        build_ib_algo_order(order, request)


def test_oversize_params_raises():
    order = _make_order()
    params = {f"key{i}": "v" for i in range(17)}
    request = _make_request("ADAPTIVE", params)
    with pytest.raises(ValueError, match="too many"):
        build_ib_algo_order(order, request)


def test_value_too_long_raises():
    order = _make_order()
    request = _make_request("ADAPTIVE", {"urgency": "X" * 65})
    with pytest.raises(ValueError, match="too long"):
        build_ib_algo_order(order, request)


def test_reverse_map_is_1to1():
    assert len(_ALGO_STRATEGY_MAP_REVERSE) == len(_ALGO_STRATEGY_MAP)


def test_reserve_includes_randomize_size():
    order = _make_order()
    order.orderType = "LMT"
    request = _make_request(
        "RESERVE",
        {"display_size": "50", "randomize_size": "true"},
        order_type="LIMIT",
    )
    build_ib_algo_order(order, request)
    tag_map = {tv.tag: tv.value for tv in order.algoParams}
    assert "displaySize" in tag_map
    assert "randomizeSize" in tag_map
