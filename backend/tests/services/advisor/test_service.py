import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.advisor import service as service_module
from app.services.advisor.prompts import SYSTEM_PROMPT
from app.services.advisor.service import AdvisorService, _contains_prompt_echo, _fallback_chain
from app.services.advisor.types import AdvisorConfig, AdvisorVerdict, ContextSummary, OrderIntent

pytestmark = pytest.mark.no_db


class _DbContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_db_factory(decision_id=123):
    row = MagicMock()
    row.scalar_one.return_value = decision_id
    session = AsyncMock()
    session.execute.return_value = row
    factory = MagicMock(return_value=_DbContext(session))
    return factory, session


def _make_service(*, redis=None, db_factory=None, ai_text=None):
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        return_value=SimpleNamespace(
            text=ai_text
            or json.dumps(
                {
                    "action": "approve",
                    "reasoning": "ok",
                    "confidence": 0.9,
                    "advice_tags": ["other"],
                }
            ),
            provider="test-provider",
            model="test-model",
            request_id=uuid4(),
            fallback_chain=[],
        )
    )
    if redis is None:
        redis = AsyncMock()
    redis.incrby.return_value = 100
    redis.expire.return_value = True
    redis.publish.return_value = 1
    if db_factory is None:
        db_factory, _ = _make_db_factory()
    return AdvisorService(ai_client, redis, db_factory), ai_client, redis


def _make_intent():
    return OrderIntent(
        canonical_id="AAPL.NASDAQ",
        side="BUY",
        qty=str(Decimal("10")),
        order_type="MKT",
        limit_price=None,
        stop_price=None,
        tif="GTC",
        algo_strategy=None,
        position_effect="OPEN",
        broker_id=str(uuid4()),
        account_id=uuid4(),
    )


def _review_kwargs(*, config=None, bot_id=None, account_id=None, intent=None):
    account_id = account_id or uuid4()
    return {
        "bot_id": bot_id or uuid4(),
        "run_id": uuid4(),
        "account_id": account_id,
        "intent": intent or _make_intent(),
        "strategy_params": {"lookback": 20},
        "effective_config": config or AdvisorConfig(mode="VETO"),
        "db": AsyncMock(),
    }


def _context_summary():
    return ContextSummary(
        bar_count=1,
        position_count=0,
        recent_fill_count=0,
        risk_decision_count=0,
        params_hash="abc",
        payload_token_estimate=10,
    )


@pytest.mark.asyncio
async def test_review_off_mode_returns_approve_without_lock_or_redis():
    service, ai_client, redis = _make_service()
    kwargs = _review_kwargs(config=AdvisorConfig())

    verdict, decision_id = await service.review(**kwargs)

    assert verdict == AdvisorVerdict(action="approve", confidence=None)
    assert decision_id is None
    assert service._in_flight == {}
    ai_client.complete.assert_not_called()
    redis.incrby.assert_not_called()
    redis.publish.assert_not_called()


@pytest.mark.asyncio
async def test_review_in_flight_lock_calls_fail_open():
    bot_id = uuid4()
    service, _, redis = _make_service()
    lock = asyncio.Lock()
    await lock.acquire()
    service._in_flight[str(bot_id)] = lock
    service._fail_open = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 77))

    verdict, decision_id = await service.review(**_review_kwargs(bot_id=bot_id))

    assert verdict.action == "fail_open"
    assert decision_id == 77
    service._fail_open.assert_awaited_once()
    assert service._fail_open.await_args.kwargs["reason"] == "advisor_in_flight"
    redis.incrby.assert_not_called()


@pytest.mark.asyncio
async def test_review_budget_exceeded_calls_fail_open():
    service, _, _ = _make_service()
    service._budget_ok_and_reserve = AsyncMock(return_value=False)
    service._fail_open = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 88))

    verdict, decision_id = await service.review(**_review_kwargs())

    assert verdict.action == "fail_open"
    assert decision_id == 88
    service._fail_open.assert_awaited_once()
    assert service._fail_open.await_args.kwargs["reason"] == "daily_budget_exceeded"


@pytest.mark.asyncio
async def test_review_happy_path_approve_persists_publishes_and_returns_id(monkeypatch):
    db_factory, session = _make_db_factory(decision_id=42)
    service, _, redis = _make_service(db_factory=db_factory)
    monkeypatch.setattr(
        service_module.ContextBuilder, "build", AsyncMock(return_value=("{}", _context_summary()))
    )

    verdict, decision_id = await service.review(**_review_kwargs())

    assert verdict.action == "approve"
    assert decision_id == 42
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()
    redis.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_happy_path_veto_persists_publishes_and_returns_id(monkeypatch):
    db_factory, session = _make_db_factory(decision_id=43)
    ai_text = json.dumps(
        {
            "action": "veto",
            "reasoning": "position size is too large",
            "confidence": 0.95,
            "advice_tags": ["size_too_large"],
        }
    )
    service, _, redis = _make_service(db_factory=db_factory, ai_text=ai_text)
    monkeypatch.setattr(
        service_module.ContextBuilder, "build", AsyncMock(return_value=("{}", _context_summary()))
    )

    verdict, decision_id = await service.review(**_review_kwargs())

    assert verdict.action == "veto"
    assert verdict.advice_tags == ["size_too_large"]
    assert decision_id == 43
    session.execute.assert_awaited_once()
    redis.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_observe_mode_veto_downgrades_to_approve(monkeypatch):
    db_factory, session = _make_db_factory(decision_id=44)
    ai_text = json.dumps(
        {
            "action": "veto",
            "reasoning": "trade is overextended",
            "confidence": 0.9,
            "advice_tags": ["overtrading"],
        }
    )
    service, _, _ = _make_service(db_factory=db_factory, ai_text=ai_text)
    monkeypatch.setattr(
        service_module.ContextBuilder, "build", AsyncMock(return_value=("{}", _context_summary()))
    )

    verdict, decision_id = await service.review(
        **_review_kwargs(config=AdvisorConfig(mode="OBSERVE"))
    )

    assert verdict.action == "approve"
    assert decision_id == 44
    params = session.execute.await_args.args[1]
    assert params["verdict"] == "approve"


@pytest.mark.asyncio
async def test_review_timeout_calls_fail_open(monkeypatch):
    service, _, _ = _make_service()
    monkeypatch.setattr(
        service_module.ContextBuilder, "build", AsyncMock(return_value=("{}", _context_summary()))
    )
    service._complete = AsyncMock(side_effect=TimeoutError)
    service._fail_open = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 55))

    await service.review(**_review_kwargs())

    service._fail_open.assert_awaited_once()
    assert service._fail_open.await_args.kwargs["reason"] == "timeout"


@pytest.mark.asyncio
async def test_review_unexpected_exception_calls_fail_open_with_class_name(monkeypatch):
    service, _, _ = _make_service()
    monkeypatch.setattr(
        service_module.ContextBuilder,
        "build",
        AsyncMock(side_effect=RuntimeError("context exploded")),
    )
    service._fail_open = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 56))

    await service.review(**_review_kwargs())

    service._fail_open.assert_awaited_once()
    assert service._fail_open.await_args.kwargs["reason"] == "unexpected:RuntimeError"


@pytest.mark.asyncio
async def test_review_schema_parse_error_calls_fail_open(monkeypatch):
    service, _, _ = _make_service(ai_text="{bad json")
    monkeypatch.setattr(
        service_module.ContextBuilder, "build", AsyncMock(return_value=("{}", _context_summary()))
    )
    service._fail_open = AsyncMock(return_value=(AdvisorVerdict(action="fail_open"), 57))

    await service.review(**_review_kwargs())

    service._fail_open.assert_awaited_once()
    reason = service._fail_open.await_args.kwargs["reason"]
    assert reason.startswith("schema_error:")


@pytest.mark.asyncio
async def test_apply_safety_rules_veto_empty_reasoning_fail_open():
    service, _, _ = _make_service()

    verdict = await service._apply_safety_rules(
        AdvisorVerdict(action="veto", reasoning="   ", confidence=0.9),
        AdvisorConfig(mode="VETO"),
    )

    assert verdict.action == "fail_open"
    assert verdict.reasoning == "veto_without_reasoning"


@pytest.mark.asyncio
async def test_apply_safety_rules_veto_low_confidence_fail_open():
    service, _, _ = _make_service()

    verdict = await service._apply_safety_rules(
        AdvisorVerdict(action="veto", reasoning="too risky", confidence=0.49),
        AdvisorConfig(mode="VETO", min_veto_confidence=0.5),
    )

    assert verdict.action == "fail_open"
    assert verdict.reasoning == "low_confidence"
    assert verdict.confidence == 0.49


@pytest.mark.asyncio
async def test_apply_safety_rules_veto_meets_min_confidence_unchanged():
    service, _, _ = _make_service()
    original = AdvisorVerdict(action="veto", reasoning="too risky", confidence=0.5)

    verdict = await service._apply_safety_rules(
        original, AdvisorConfig(mode="VETO", min_veto_confidence=0.5)
    )

    assert verdict is original


@pytest.mark.asyncio
async def test_apply_safety_rules_prompt_echo_detected_fail_open():
    service, _, _ = _make_service()
    echoed = f"analysis: {SYSTEM_PROMPT[:51]}"

    verdict = await service._apply_safety_rules(
        AdvisorVerdict(action="approve", reasoning=echoed, confidence=0.8),
        AdvisorConfig(mode="VETO"),
    )

    assert verdict.action == "fail_open"
    assert verdict.reasoning == "prompt_echo_detected"


@pytest.mark.asyncio
async def test_apply_safety_rules_unknown_advice_tags_map_to_other():
    service, _, _ = _make_service()

    verdict = await service._apply_safety_rules(
        AdvisorVerdict(
            action="approve",
            reasoning="ok",
            confidence=0.8,
            advice_tags=["not_a_real_tag", "overtrading"],
        ),
        AdvisorConfig(mode="VETO"),
    )

    assert verdict.advice_tags == ["other", "overtrading"]


@pytest.mark.asyncio
async def test_apply_safety_rules_known_advice_tags_pass_through_unchanged():
    service, _, _ = _make_service()
    original = AdvisorVerdict(
        action="approve",
        reasoning="ok",
        confidence=0.8,
        advice_tags=["overtrading", "size_too_large"],
    )

    verdict = await service._apply_safety_rules(original, AdvisorConfig(mode="VETO"))

    assert verdict is original


@pytest.mark.asyncio
async def test_budget_ok_and_reserve_counter_within_limit_returns_true():
    redis = AsyncMock()
    redis.incrby.return_value = 500
    service, _, _ = _make_service(redis=redis)

    assert await service._budget_ok_and_reserve(uuid4(), AdvisorConfig(mode="VETO")) is True
    redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_ok_and_reserve_counter_over_limit_returns_false():
    redis = AsyncMock()
    redis.incrby.return_value = 501  # > 500 cents (default $5.00 budget)
    service = AdvisorService(MagicMock(), redis, MagicMock())

    assert await service._budget_ok_and_reserve(uuid4(), AdvisorConfig(mode="VETO")) is False
    redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_ok_and_reserve_redis_exception_returns_true():
    redis = AsyncMock()
    redis.incrby.side_effect = RuntimeError("redis down")
    service, _, _ = _make_service(redis=redis)

    assert await service._budget_ok_and_reserve(uuid4(), AdvisorConfig(mode="VETO")) is True
    redis.expire.assert_not_called()


def test_contains_prompt_echo_short_string_false():
    assert _contains_prompt_echo(SYSTEM_PROMPT[:50]) is False


def test_contains_prompt_echo_long_non_prompt_string_false():
    assert _contains_prompt_echo("x" * 200) is False


def test_contains_prompt_echo_with_51_char_prompt_substring_true():
    assert _contains_prompt_echo(f"prefix {SYSTEM_PROMPT[10:61]} suffix") is True


def test_fallback_chain_none_returns_empty_list():
    assert _fallback_chain(None) == []


def test_fallback_chain_without_attr_returns_empty_list():
    assert _fallback_chain(SimpleNamespace(text="ok")) == []


def test_fallback_chain_with_hop_objects_returns_json_strings():
    hop = MagicMock()
    hop.model_dump.return_value = {"provider": "p1", "model": "m1"}
    result = SimpleNamespace(fallback_chain=[hop])

    assert _fallback_chain(result) == ['{"provider": "p1", "model": "m1"}']
    hop.model_dump.assert_called_once_with(mode="json")
