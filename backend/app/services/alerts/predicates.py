"""Predicate primitives + JSON-Schema validator + evaluator dispatch.

The 10 primitives are: price_threshold, pct_change_window, ma_cross,
volume_spike, order_event, ai_signal, news_event, unknown, composite_and,
composite_or. Each primitive is a pure function over a ``state`` dict;
``evaluate()`` dispatches on ``predicate["kind"]``.

Used by:
- ``app.services.alerts.parser`` to validate AI-generated predicates
- ``app.services.alerts.evaluator`` to evaluate predicates on every tick
- ``app.services.alerts.rules`` to compute ``referenced_capabilities``
  for the requires_capabilities column on alembic 0044
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent / "predicates.schema.json"
_SCHEMA: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text())
_VALIDATOR = jsonschema.Draft7Validator(_SCHEMA)


class PredicateValidationError(Exception):
    """Raised when a predicate does not satisfy the JSON schema."""

    def __init__(self, schema_errors: list[str]) -> None:
        super().__init__(f"predicate invalid: {schema_errors}")
        self.schema_errors = schema_errors


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}


def _eval_price_threshold(p: dict[str, Any], state: dict[str, Any]) -> bool:
    prices = state.get("prices", {})
    symbol = p["symbol"]
    if symbol not in prices:
        return False
    price = prices[symbol]
    op_func = _OPS[p["op"]]
    return bool(op_func(price, p["value"]))


def _eval_pct_change_window(p: dict[str, Any], state: dict[str, Any]) -> bool:
    bars = state.get("bars", {})
    symbol = p["symbol"]
    if symbol not in bars:
        return False
    series = bars[symbol]
    if len(series) < 2:
        return False
    closes = [b["close"] for b in series]
    first = closes[0]
    last = closes[-1]
    if first == 0:
        return False
    pct = (last - first) / first * 100
    target_pct = p["pct"]
    if target_pct >= 0:
        return bool(pct >= target_pct)
    return bool(pct <= target_pct)


def _eval_ma_cross(p: dict[str, Any], state: dict[str, Any]) -> bool:
    bars = state.get("bars", {})
    symbol = p["symbol"]
    if symbol not in bars:
        return False
    fast_period = p["fast_period"]
    slow_period = p["slow_period"]
    series = bars[symbol]
    if len(series) < slow_period + 1:
        return False
    closes = [b["close"] for b in series]
    fast_now = sum(closes[-fast_period:]) / fast_period
    slow_now = sum(closes[-slow_period:]) / slow_period
    fast_prev = sum(closes[-fast_period - 1 : -1]) / fast_period
    slow_prev = sum(closes[-slow_period - 1 : -1]) / slow_period
    if p["direction"] == "golden":
        return bool(fast_prev <= slow_prev and fast_now > slow_now)
    if p["direction"] == "death":
        return bool(fast_prev >= slow_prev and fast_now < slow_now)
    return False


def _eval_volume_spike(p: dict[str, Any], state: dict[str, Any]) -> bool:
    bars = state.get("bars", {})
    symbol = p["symbol"]
    if symbol not in bars:
        return False
    vs_window_minutes = p["vs_window_minutes"]
    series = bars[symbol]
    if len(series) < vs_window_minutes + 1:
        return False
    window = series[-vs_window_minutes - 1 : -1]
    avg = sum(b["volume"] for b in window) / len(window)
    if avg == 0:
        return False
    return bool(series[-1]["volume"] >= avg * p["multiple"])


def _eval_order_event(p: dict[str, Any], state: dict[str, Any]) -> bool:
    event = state.get("order_event")
    if event is None:
        return False
    if event.get("event_type") != p["event_type"]:
        return False
    for key in ("account_id", "broker_id", "symbol"):
        if p.get(key) is not None and event.get(key) != p[key]:
            return False
    return True


def _eval_ai_signal(p: dict[str, Any], state: dict[str, Any]) -> bool:
    signals = state.get("ai_signals", {})
    key = p["prompt_template"]
    if key not in signals:
        return False
    score = signals[key]
    return bool(score >= p["threshold"])


def _eval_news_event(p: dict[str, Any], state: dict[str, Any]) -> bool:
    if not state.get("capabilities", {}).get("news_feed"):
        return False
    news_items = state.get("news", [])
    for item in news_items:
        if p.get("symbol") is not None and item.get("symbol") != p["symbol"]:
            continue
        if p.get("source") is not None and item.get("source") != p["source"]:
            continue
        if p.get("sentiment") is not None and item.get("sentiment") != p["sentiment"]:
            continue
        return True
    return False


def _eval_unknown(p: dict[str, Any], state: dict[str, Any]) -> bool:
    return False


def _eval_composite_and(p: dict[str, Any], state: dict[str, Any]) -> bool:
    return all(evaluate(child, state) for child in p["children"])


def _eval_composite_or(p: dict[str, Any], state: dict[str, Any]) -> bool:
    return any(evaluate(child, state) for child in p["children"])


_DISPATCH = {
    "price_threshold": _eval_price_threshold,
    "pct_change_window": _eval_pct_change_window,
    "ma_cross": _eval_ma_cross,
    "volume_spike": _eval_volume_spike,
    "order_event": _eval_order_event,
    "ai_signal": _eval_ai_signal,
    "news_event": _eval_news_event,
    "unknown": _eval_unknown,
    "composite_and": _eval_composite_and,
    "composite_or": _eval_composite_or,
}


def validate_schema(predicate: dict[str, Any]) -> None:
    """Raise PredicateValidationError if predicate fails JSON-schema."""
    errors = list(_VALIDATOR.iter_errors(predicate))
    if errors:
        schema_errors = [str(err) for err in errors]
        raise PredicateValidationError(schema_errors)


def evaluate(predicate: dict[str, Any], state: dict[str, Any]) -> bool:
    """Dispatch predicate evaluation. Raises PredicateValidationError for unknown kind."""
    kind = predicate.get("kind", "")
    evaluator = _DISPATCH.get(kind)
    if evaluator is None:
        raise PredicateValidationError([f"unknown kind: {kind!r}"])
    return evaluator(predicate, state)


def referenced_symbols(predicate: dict[str, Any]) -> set[str]:
    """Walk predicate tree, return all symbols referenced (for inverted index)."""
    kind = predicate.get("kind", "")
    if kind in ("price_threshold", "pct_change_window", "ma_cross", "volume_spike"):
        return {predicate["symbol"]}
    if kind in ("order_event", "news_event"):
        symbol = predicate.get("symbol")
        return {symbol} if symbol is not None else set()
    if kind in ("composite_and", "composite_or"):
        symbols: set[str] = set()
        for child in predicate.get("children", []):
            symbols.update(referenced_symbols(child))
        return symbols
    return set()


def referenced_capabilities(predicate: dict[str, Any]) -> list[dict[str, Any]]:
    """Return [{capability, params}, ...] for storage in alerts.requires_capabilities."""
    kind = predicate.get("kind", "")
    if kind == "news_event":
        return [{"capability": "news_feed", "params": {}}]
    if kind == "ai_signal":
        return [{"capability": "ai_router", "params": {"capability": predicate["capability"]}}]
    if kind in ("composite_and", "composite_or"):
        caps: list[dict[str, Any]] = []
        for child in predicate.get("children", []):
            caps.extend(referenced_capabilities(child))
        return caps
    return []
