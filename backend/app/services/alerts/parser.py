# ruff: noqa: E501
"""Phase 11b chunk A: hard-LOCAL_ONLY parse-once-freeze NL → predicate JSON.

Three-layer defence-in-depth (matches 11a):
1. API boundary asserts ``force_local_only=True`` before calling parser.
2. Parser passes ``force_local_only=True`` to AICompletionClient.
3. LiteLLM auth-callback rejects cloud routes for LOCAL_ONLY requests.

Portfolio context stripping (MED-4): the request payload sent to the
AI client contains ONLY ``original_nl`` + ``symbols_user_watches`` —
no NLV, no positions, no account_ids, no cost basis, no broker_id.

Parse-once-freeze: the AI runs ONCE per rule at create-time and emits
a structured predicate JSON. Runtime evaluator never re-parses.

Outcomes:
- ``ok``: schema-valid + no ``unknown`` leaves
- ``uncertain``: schema-valid but contains ``unknown`` leaves (user disambiguates)
- ``failed``: two schema-invalid attempts (FE opens ParseFailedEditor)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.alerts.exceptions import ParserUnavailableError
from app.services.alerts.predicates import PredicateValidationError, validate_schema


@dataclass(slots=True)
class ParseResult:
    parse_status: str  # 'ok' | 'uncertain' | 'failed'
    predicate_json: dict[str, Any] | None
    partial_predicate: dict[str, Any] | None
    suggestions: list[str]
    parse_metadata: dict[str, Any]


class _AICompletionLike(Protocol):
    async def complete(
        self,
        *,
        capability: str,
        prompt: str,
        force_local_only: bool,
        response_format: str,
        system: str,
    ) -> Any: ...


_SYSTEM_PROMPT = """You convert a natural-language trading alert into a structured predicate JSON.

You MUST respond with valid JSON matching exactly one of these primitive kinds:
- price_threshold: {kind, symbol, op (gt/lt/gte/lte/eq), value, lookback_seconds?}
- pct_change_window: {kind, symbol, pct, window_seconds (>= 60)}
- ma_cross: {kind, symbol, fast_period (1-500), slow_period (1-500), direction (golden/death)}
- volume_spike: {kind, symbol, multiple (>1), vs_window_minutes}
- order_event: {kind, event_type (filled/cancelled/rejected/modified), account_id?, broker_id?, symbol?}
- ai_signal: {kind, prompt_template, capability (STRUCTURED_OUTPUT/REASONING/NUMERICAL), threshold 0-1}
- news_event: {kind, symbol?, source?, sentiment?}
- unknown: {kind, raw_text, suggestions[]} (use ONLY when you can't classify)
- composite_and: {kind, children[]}  (1-10 children)
- composite_or: {kind, children[]}

If you can't classify any part, use `unknown` for that leaf and put your best guesses in `suggestions`.
Respond with ONLY the JSON object — no prose, no markdown fences."""


def _build_user_prompt(original_nl: str, symbols_user_watches: list[str]) -> str:
    """Serialise the user-facing prompt payload.

    PII strip: keys are ``rule_text`` + ``symbols_user_watches`` ONLY.
    """
    return json.dumps(
        {
            "rule_text": original_nl,
            "symbols_user_watches": symbols_user_watches,
        }
    )


def _has_unknown_leaves(predicate: dict[str, Any]) -> bool:
    if predicate.get("kind") == "unknown":
        return True
    if predicate.get("kind") in {"composite_and", "composite_or"}:
        return any(_has_unknown_leaves(c) for c in predicate.get("children", []))
    return False


async def parse_nl(
    *,
    client: _AICompletionLike,
    original_nl: str,
    symbols_user_watches: list[str],
) -> ParseResult:
    user_prompt = _build_user_prompt(original_nl, symbols_user_watches)
    second_system = _SYSTEM_PROMPT
    partial: dict[str, Any] | None = None

    for attempt in (1, 2):
        try:
            result = await client.complete(
                capability="STRUCTURED_OUTPUT",
                prompt=user_prompt,
                force_local_only=True,
                response_format="json",
                system=second_system,
            )
        except Exception as exc:
            raise ParserUnavailableError(str(exc)) from exc

        try:
            predicate = json.loads(result.text)
        except json.JSONDecodeError as exc:
            partial = {"kind": "unknown", "raw_text": result.text, "suggestions": []}
            second_system = (
                _SYSTEM_PROMPT
                + f"\n\nYour previous response was not valid JSON: {exc.msg}. Try again."
            )
            continue

        try:
            validate_schema(predicate)
        except PredicateValidationError as exc:
            partial = predicate
            second_system = (
                _SYSTEM_PROMPT + f"\n\nYour previous response failed schema validation: "
                f"{exc.schema_errors}. Try again."
            )
            continue

        if _has_unknown_leaves(predicate):
            return ParseResult(
                parse_status="uncertain",
                predicate_json=predicate,
                partial_predicate=None,
                suggestions=[],
                parse_metadata={
                    "model": getattr(result, "model", None),
                    "latency_ms": getattr(result, "latency_ms", None),
                    "attempt": attempt,
                },
            )
        return ParseResult(
            parse_status="ok",
            predicate_json=predicate,
            partial_predicate=None,
            suggestions=[],
            parse_metadata={
                "model": getattr(result, "model", None),
                "latency_ms": getattr(result, "latency_ms", None),
                "attempt": attempt,
            },
        )

    return ParseResult(
        parse_status="failed",
        predicate_json=None,
        partial_predicate=partial,
        suggestions=[],
        parse_metadata={"reason": "two_attempts_invalid"},
    )
