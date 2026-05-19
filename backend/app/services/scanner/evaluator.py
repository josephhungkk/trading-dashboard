from __future__ import annotations

from typing import Any

from lark import Lark, Token, Transformer, Tree

GRAMMAR = r"""
rule: or_expr

or_expr:  and_expr ("or" and_expr)*    -> or_expr
and_expr: not_expr ("and" not_expr)*   -> and_expr
not_expr: "not" not_expr               -> negate
        | atom                         -> passthrough
atom: comparison                       -> passthrough
    | "(" or_expr ")"                  -> passthrough

comparison: term OP term               -> cmp_expr

term: func_call                        -> passthrough
    | NUMBER                           -> number
    | NAME                             -> name

func_call: NAME "(" arglist? ")"       -> call

arglist: term ("," term)*

OP: "<" | ">" | "<=" | ">=" | "==" | "!="

%import common.CNAME  -> NAME
%import common.NUMBER
%import common.WS
%ignore WS
"""

_PARSER = Lark(GRAMMAR, parser="lalr", start="rule")

MAX_DEPTH = 8
MAX_NODES = 256
MAX_FUNC_CALLS = 32
MAX_PERIOD_SUM = 5000


class EvaluatorParseError(Exception):
    pass


class EvaluatorBudgetError(Exception):
    pass


def _extract_period_sum(arglist_node: Tree) -> float:
    """Sum numeric literals inside an arglist Tree (used for period budget)."""
    total = 0.0
    for child in arglist_node.children:
        if isinstance(child, Tree) and child.data == "number":
            for tok in child.children:
                if isinstance(tok, Token) and tok.type == "NUMBER":
                    total += float(tok)
    return total


def _check_budget(tree: Tree) -> None:
    # Phase 1: count total nodes — raises max_nodes before any other check.
    total_nodes = sum(1 for _ in tree.iter_subtrees())
    if total_nodes > MAX_NODES:
        raise EvaluatorBudgetError(f"max_nodes exceeded ({MAX_NODES})")

    # Phase 2: check depth, func_calls, period_sum.
    func_calls = 0
    period_sum = 0.0

    def _walk(node: Tree | Token, depth: int) -> None:
        nonlocal func_calls, period_sum
        if depth > MAX_DEPTH:
            raise EvaluatorBudgetError(f"max_depth exceeded ({MAX_DEPTH})")
        if isinstance(node, Tree):
            if node.data == "call":
                func_calls += 1
                if func_calls > MAX_FUNC_CALLS:
                    raise EvaluatorBudgetError(f"max_func_calls exceeded ({MAX_FUNC_CALLS})")
                # accumulate period sum from all arglist numeric children
                for child in node.children:
                    if isinstance(child, Tree) and child.data == "arglist":
                        period_sum += _extract_period_sum(child)
                        if period_sum > MAX_PERIOD_SUM:
                            raise EvaluatorBudgetError(
                                f"max_period_sum exceeded ({MAX_PERIOD_SUM})"
                            )
            for child in node.children:
                _walk(child, depth + 1)

    _walk(tree, 0)


def _unwrap(val: Any) -> Any:
    """Unwrap a Tree node that contains a single value (passthrough rule)."""
    if isinstance(val, Tree):
        if len(val.children) == 1:
            return _unwrap(val.children[0])
        return None
    return val


class _EvalTransformer(Transformer):
    def __init__(self, symbols: dict[str, Any]) -> None:
        super().__init__()
        self._sym = symbols

    def or_expr(self, items: list) -> bool:
        result = _unwrap(items[0])
        for item in items[1:]:
            result = result or _unwrap(item)
        return bool(result)

    def and_expr(self, items: list) -> bool:
        result = _unwrap(items[0])
        for item in items[1:]:
            result = result and _unwrap(item)
        return bool(result)

    def negate(self, items: list) -> bool:
        """Handles: not_expr: "not" not_expr -> negate"""
        return not _unwrap(items[0])

    def passthrough(self, items: list) -> Any:
        """Handles passthrough aliases (atom, term single-child cases)."""
        return _unwrap(items[0])

    def cmp_expr(self, items: list) -> bool:
        left = _unwrap(items[0])
        op = items[1]
        right = _unwrap(items[2])
        if left is None or right is None:
            return False
        op_s = str(op)
        try:
            if op_s == "<":
                return left < right  # type: ignore[operator]
            if op_s == ">":
                return left > right  # type: ignore[operator]
            if op_s == "<=":
                return left <= right  # type: ignore[operator]
            if op_s == ">=":
                return left >= right  # type: ignore[operator]
            if op_s == "==":
                return left == right
            if op_s == "!=":
                return left != right
        except TypeError:
            return False
        except ValueError:
            return False
        return False

    def call(self, items: list) -> Any:
        name = str(items[0])
        # items[1] is the arglist Tree if present; its children are already transformed
        args: list[Any] = []
        if len(items) > 1 and isinstance(items[1], Tree):
            # arglist children are already-transformed values
            args = [_unwrap(c) for c in items[1].children]
        fn = self._sym.get(name)
        if fn is None:
            return None
        if callable(fn):
            try:
                return fn(*args)
            except Exception:
                return None
        return None

    def name(self, items: list) -> Any:
        key = str(items[0])
        return self._sym.get(key)

    def number(self, items: list) -> float:
        return float(items[0])

    def __default_token__(self, token: Token) -> Token:
        return token


class ScannerEvaluator:
    def parse(self, rule_expr: str) -> Tree:
        try:
            tree = _PARSER.parse(rule_expr)
        except Exception as exc:
            raise EvaluatorParseError(str(exc)) from exc
        _check_budget(tree)
        return tree

    def evaluate(self, tree: Tree, symbols: dict[str, Any]) -> bool:
        transformer = _EvalTransformer(symbols)
        try:
            result = transformer.transform(tree)
            # The top-level "rule" node has no transformer method → stays as Tree.
            # Unwrap to get the evaluated bool from or_expr inside it.
            result = _unwrap(result)
        except Exception:
            return False
        return bool(result)
