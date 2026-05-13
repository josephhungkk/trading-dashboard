"""Phase 11b chunk A: parser tests — hard-LOCAL_ONLY + portfolio-context-stripping.

The PII-stripping test (test_parser_request_payload_strips_portfolio_context)
is security-critical: the request payload must contain only the rule_text +
symbols_user_watches, never NLV / positions / account_ids / cost_basis /
broker_id even if the user's NL input mentions them.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.alerts.exceptions import ParserUnavailableError
from app.services.alerts.parser import parse_nl


def _fake_ai_response(text: str, model: str = "qwen2.5:7b", latency_ms: int = 1) -> object:
    """Build a duck-typed object mimicking AICompletionClient's response."""
    return type(
        "R",
        (),
        {"text": text, "model": model, "latency_ms": latency_ms, "fallback_chain": []},
    )()


@pytest.fixture
def fake_ai_client():
    return AsyncMock()


pytestmark = pytest.mark.asyncio


async def test_parse_canonical_predicate(fake_ai_client) -> None:
    fake_ai_client.complete.return_value = _fake_ai_response(
        json.dumps({"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200.0})
    )
    result = await parse_nl(
        client=fake_ai_client,
        original_nl="alert when AAPL > 200",
        symbols_user_watches=["AAPL"],
    )
    assert result.parse_status == "ok"
    assert result.predicate_json == {
        "kind": "price_threshold",
        "symbol": "AAPL",
        "op": "gt",
        "value": 200.0,
    }
    assert result.parse_metadata["attempt"] == 1


async def test_parse_schema_invalid_retries_once(fake_ai_client) -> None:
    fake_ai_client.complete.side_effect = [
        _fake_ai_response('{"kind": "bogus"}'),
        _fake_ai_response(
            json.dumps({"kind": "price_threshold", "symbol": "AAPL", "op": "gt", "value": 200.0})
        ),
    ]
    result = await parse_nl(client=fake_ai_client, original_nl="...", symbols_user_watches=[])
    assert result.parse_status == "ok"
    assert result.parse_metadata["attempt"] == 2
    assert fake_ai_client.complete.call_count == 2


async def test_parse_second_attempt_fails_returns_failed(fake_ai_client) -> None:
    fake_ai_client.complete.return_value = _fake_ai_response('{"kind": "bogus"}')
    result = await parse_nl(client=fake_ai_client, original_nl="...", symbols_user_watches=[])
    assert result.parse_status == "failed"
    assert result.partial_predicate is not None
    assert fake_ai_client.complete.call_count == 2


async def test_parse_unknown_leaves_returns_uncertain(fake_ai_client) -> None:
    fake_ai_client.complete.return_value = _fake_ai_response(
        json.dumps({"kind": "unknown", "raw_text": "huh", "suggestions": ["a", "b"]})
    )
    result = await parse_nl(client=fake_ai_client, original_nl="...", symbols_user_watches=[])
    assert result.parse_status == "uncertain"
    assert result.predicate_json is not None
    assert result.predicate_json["kind"] == "unknown"


async def test_parse_propagates_router_unavailable(fake_ai_client) -> None:
    fake_ai_client.complete.side_effect = RuntimeError("router down")
    with pytest.raises(ParserUnavailableError):
        await parse_nl(client=fake_ai_client, original_nl="x", symbols_user_watches=[])


async def test_parser_request_payload_strips_portfolio_context(fake_ai_client) -> None:
    fake_ai_client.complete.return_value = _fake_ai_response(
        json.dumps({"kind": "unknown", "raw_text": "x", "suggestions": []})
    )
    await parse_nl(
        client=fake_ai_client,
        original_nl="alert when my IRA at Schwab drops below 200K NLV (account abc-123)",
        symbols_user_watches=["AAPL"],
    )
    call_kwargs = fake_ai_client.complete.call_args.kwargs
    # The 'prompt' kwarg is the only place user text appears. Its JSON-decoded
    # body must contain rule_text + symbols_user_watches, nothing else.
    prompt_payload = json.loads(call_kwargs["prompt"])
    assert set(prompt_payload.keys()) == {"rule_text", "symbols_user_watches"}
    # The system prompt legitimately documents primitive shapes (including
    # account_id/broker_id as order_event SLOTS), so we don't ban those tokens
    # there. What we DO ban is the user's portfolio context bleeding into any
    # part of the request beyond rule_text itself. Pick PII tokens that only
    # appear if a portfolio-context leak happened.
    envelope_minus_rule_text = {k: v for k, v in call_kwargs.items() if k != "prompt"}
    envelope_text = json.dumps(envelope_minus_rule_text).lower()
    # These tokens come from the user's NL on this specific test; they should
    # only appear inside rule_text (which we already isolated above), not in
    # any other part of the request.
    for leaked_token in ("ira", "schwab", "abc-123", "200k"):
        assert leaked_token not in envelope_text, (
            f"parser request envelope leaked user PII {leaked_token!r}: {envelope_text}"
        )
    assert call_kwargs["force_local_only"] is True
    assert call_kwargs["capability"] == "STRUCTURED_OUTPUT"
