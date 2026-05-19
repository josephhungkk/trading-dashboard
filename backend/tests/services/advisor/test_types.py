import dataclasses
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.advisor.types import (
    AdvisorConfig,
    AdvisorDecision,
    AdvisorMode,
    AdvisorVerdict,
    AdvisorVetoedResult,
    ContextSummary,
    OrderIntent,
)
from app.services.ai.capabilities import AICapability


def test_advisor_mode_off_is_default():
    cfg = AdvisorConfig()
    assert cfg.mode == AdvisorMode.OFF


def test_advisor_config_capability_is_ai_capability_enum():
    cfg = AdvisorConfig(capability=AICapability.REASONING)
    assert cfg.capability == AICapability.REASONING


def test_advisor_config_rejects_bad_capability():
    with pytest.raises(ValidationError):
        AdvisorConfig(capability="NOT_A_CAPABILITY")


def test_advisor_config_local_only_default_false():
    assert AdvisorConfig().local_only is False


def test_advisor_config_daily_budget_stored_as_decimal():
    cfg = AdvisorConfig(daily_budget_usd=Decimal("5.00"))
    assert cfg.daily_budget_usd == Decimal("5.00")


def test_advisor_verdict_approve_ok():
    v = AdvisorVerdict(action="approve", reasoning="looks good", confidence=0.9)
    assert v.action == "approve"


def test_advisor_verdict_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        AdvisorVerdict(action="veto", reasoning="bad", confidence=1.5)


def test_order_intent_qty_round_trips_as_string():
    intent = OrderIntent(
        canonical_id="AAPL.NASDAQ",
        side="BUY",
        qty="100.5",
        order_type="LMT",
        limit_price="182.50",
        stop_price=None,
        tif="DAY",
        algo_strategy=None,
        position_effect="OPEN",
        broker_id="ibkr",
        account_id=uuid4(),
    )
    raw = json.loads(intent.model_dump_json())
    assert raw["qty"] == "100.5"
    assert raw["limit_price"] == "182.50"


def test_context_summary_validates():
    cs = ContextSummary(
        bar_count=50,
        position_count=2,
        recent_fill_count=3,
        risk_decision_count=1,
        params_hash="abc123",
        payload_token_estimate=1200,
    )
    assert cs.bar_count == 50


def test_advisor_vetoed_result_is_frozen_dataclass():
    r = AdvisorVetoedResult(decision_id=1, reasoning="too risky", advice_tags=["overtrading"])
    assert dataclasses.is_dataclass(r)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        r.decision_id = 99  # type: ignore[misc]


def test_advisor_decision_account_gate_outcome_field():
    import datetime

    ad = AdvisorDecision(
        id=1,
        bot_id=uuid4(),
        bot_run_id=None,
        account_id=uuid4(),
        canonical_id="X",
        intent={},
        context_summary=ContextSummary(
            bar_count=0,
            position_count=0,
            recent_fill_count=0,
            risk_decision_count=0,
            params_hash="",
            payload_token_estimate=0,
        ),
        prompt_version=1,
        verdict="approve",
        reasoning="",
        confidence=None,
        advice_tags=[],
        provider=None,
        model=None,
        fallback_chain=[],
        latency_ms=100,
        ai_completion_ts=None,
        ai_completion_request_id=None,
        account_gate_outcome="not_evaluated",
        account_gate_decision_id=None,
        effective_mode="OBSERVE",
        created_at=datetime.datetime.utcnow(),
    )
    assert ad.account_gate_outcome == "not_evaluated"
    assert ad.effective_mode == "OBSERVE"
