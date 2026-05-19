import pytest

from app.services.scanner.evaluator import (
    EvaluatorBudgetError,
    EvaluatorParseError,
    ScannerEvaluator,
)

INDICATOR_VALS = {
    "rsi": lambda period: 28.0,
    "sma": lambda field, period: 150.0,
    "ema": lambda field, period: 148.0,
    "volume_ratio": lambda period: 2.3,
    "close": 152.0,
    "volume": 5_000_000.0,
    "mcap": None,
}

evaluator = ScannerEvaluator()


def test_simple_rsi_rule():
    ast = evaluator.parse("rsi(14) < 30")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_and_rule():
    ast = evaluator.parse("rsi(14) < 30 and volume_ratio(20) > 2.0")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_or_rule():
    ast = evaluator.parse("rsi(14) < 10 or volume_ratio(20) > 2.0")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_not_rule():
    ast = evaluator.parse("not rsi(14) > 50")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_precedence_not_and_or():
    ast = evaluator.parse("not rsi(14) > 30 and volume_ratio(20) > 2.0 or close > 100")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is True


def test_nullable_mcap_evaluates_false():
    ast = evaluator.parse("mcap > 1000000")
    assert evaluator.evaluate(ast, INDICATOR_VALS) is False


def test_parse_error_raises():
    with pytest.raises(EvaluatorParseError):
        evaluator.parse("rsi(14 <<< 30")


def test_budget_node_cap():
    expr = " and ".join(["rsi(14) < 30"] * 130)
    with pytest.raises(EvaluatorBudgetError, match="max_nodes"):
        evaluator.parse(expr)


def test_budget_depth_cap():
    expr = "(" * 9 + "rsi(14) < 30" + ")" * 9
    with pytest.raises(EvaluatorBudgetError, match="max_depth"):
        evaluator.parse(expr)


def test_budget_func_call_cap():
    expr = " and ".join(["rsi(14) < 30"] * 33)
    with pytest.raises(EvaluatorBudgetError, match="max_func_calls"):
        evaluator.parse(expr)


def test_budget_period_sum_cap():
    with pytest.raises(EvaluatorBudgetError, match="max_period_sum"):
        evaluator.parse("sma(close, 5001) > 100")
