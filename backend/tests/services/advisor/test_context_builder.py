import json
from uuid import uuid4

import pytest

from app.services.advisor.context_builder import ContextBuilder
from app.services.advisor.types import ContextSummary, OrderIntent


def _make_intent():
    return OrderIntent(
        canonical_id="AAPL.NASDAQ",
        side="BUY",
        qty="100",
        order_type="LMT",
        limit_price="182.50",
        stop_price=None,
        tif="DAY",
        algo_strategy=None,
        position_effect="OPEN",
        broker_id="ibkr",
        account_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_build_returns_str_and_summary(mock_db_session):
    intent = _make_intent()
    payload, summary = await ContextBuilder.build(intent, {"param_a": 1}, mock_db_session)
    assert isinstance(payload, str)
    assert isinstance(summary, ContextSummary)


@pytest.mark.asyncio
async def test_build_includes_canonical_id(mock_db_session):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session)
    assert "AAPL" in payload


@pytest.mark.asyncio
async def test_build_truncates_bars_at_50(mock_db_session_with_100_bars):
    intent = _make_intent()
    payload, summary = await ContextBuilder.build(intent, {}, mock_db_session_with_100_bars)
    bars_data = json.loads(payload).get("bars", [])
    assert len(bars_data) <= 50
    assert summary.bar_count <= 50


@pytest.mark.asyncio
async def test_build_truncates_fills_at_10(mock_db_session_with_20_fills):
    intent = _make_intent()
    payload, summary = await ContextBuilder.build(intent, {}, mock_db_session_with_20_fills)
    fills_data = json.loads(payload).get("recent_fills", [])
    assert len(fills_data) <= 10
    assert summary.recent_fill_count <= 10


@pytest.mark.asyncio
async def test_build_pii_strips_account_number(mock_db_session):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session)
    assert "account_number" not in payload


@pytest.mark.asyncio
async def test_build_sanitises_free_text_collapses_newlines(mock_db_session_with_reasoning):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session_with_reasoning)
    parsed = json.loads(payload)
    for decision in parsed.get("risk_decisions_recent", []):
        reasoning = decision.get("reasoning", "")
        assert "\n\n" not in reasoning


@pytest.mark.asyncio
async def test_build_sanitises_code_fences(mock_db_session_with_fences):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session_with_fences)
    assert "```" not in payload


@pytest.mark.asyncio
async def test_build_sanitises_caps_field_at_200_chars(mock_db_session_with_long_text):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session_with_long_text)
    parsed = json.loads(payload)
    for decision in parsed.get("risk_decisions_recent", []):
        assert len(decision.get("reasoning", "")) <= 200


@pytest.mark.asyncio
async def test_build_redacts_role_tokens(mock_db_session_with_injection):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session_with_injection)
    assert "<system>" not in payload
    assert "<user>" not in payload
    assert "[redacted_role_tag]" in payload


@pytest.mark.asyncio
async def test_build_includes_risk_limits_pnl_kill_switches(mock_db_session_with_risk_data):
    intent = _make_intent()
    payload, _ = await ContextBuilder.build(intent, {}, mock_db_session_with_risk_data)
    parsed = json.loads(payload)
    assert "risk_limits" in parsed
    assert "pnl_intraday" in parsed
    assert "kill_switches" in parsed
