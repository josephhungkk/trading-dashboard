import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from app.services.advisor import service as service_module
from app.services.advisor.service import AdvisorService
from app.services.advisor.types import AdvisorConfig, AdvisorMode, ContextSummary, OrderIntent

pytestmark = pytest.mark.no_db


class _DbContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _db_factory(decision_id: int = 999):
    row = MagicMock()
    row.scalar_one.return_value = decision_id
    session = AsyncMock()
    session.execute.return_value = row
    return MagicMock(return_value=_DbContext(session))


def _redis():
    redis = AsyncMock()
    redis.incrby.return_value = 100
    redis.expire.return_value = True
    redis.publish.return_value = 1
    redis.xadd.return_value = b"1-0"
    return redis


def _service():
    ai_client = SimpleNamespace(complete=AsyncMock())
    return AdvisorService(ai_client, _redis(), _db_factory()), ai_client


def _intent(account_id=None):
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
        account_id=account_id or uuid4(),
    )


def _context_summary():
    return ContextSummary(
        bar_count=1,
        position_count=0,
        recent_fill_count=0,
        risk_decision_count=0,
        params_hash="abc",
        payload_token_estimate=10,
    )


def _kwargs(*, bot_id=None, account_id=None, config=None):
    account_id = account_id or uuid4()
    return {
        "bot_id": bot_id or uuid4(),
        "run_id": uuid4(),
        "account_id": account_id,
        "intent": _intent(account_id),
        "strategy_params": {"lookback": 20},
        "effective_config": config or AdvisorConfig(mode=AdvisorMode.SHADOW, max_concurrent=2),
        "db": AsyncMock(),
    }


@pytest.mark.asyncio
async def test_shadow_mode_no_ai_call(monkeypatch):
    service, ai_client = _service()
    monkeypatch.setattr(
        service_module.ContextBuilder,
        "build",
        AsyncMock(return_value=("payload", _context_summary())),
    )

    verdict, _ = await service.review(**_kwargs())

    assert verdict.action == "approve"
    assert verdict.reasoning == "shadow_mode"
    ai_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_mode_audit_row_persisted(monkeypatch):
    service, _ = _service()
    monkeypatch.setattr(
        service_module.ContextBuilder,
        "build",
        AsyncMock(return_value=("payload", _context_summary())),
    )

    _, decision_id = await service.review(**_kwargs())

    assert decision_id == 999


@pytest.mark.asyncio
async def test_shadow_mode_latency_metric(monkeypatch):
    service, _ = _service()
    monkeypatch.setattr(
        service_module.ContextBuilder,
        "build",
        AsyncMock(return_value=("payload", _context_summary())),
    )
    before = REGISTRY.get_sample_value("advisor_shadow_context_build_seconds_count") or 0.0

    await service.review(**_kwargs())

    after = REGISTRY.get_sample_value("advisor_shadow_context_build_seconds_count") or 0.0
    assert after == before + 1.0


@pytest.mark.asyncio
async def test_shadow_semaphore_held(monkeypatch):
    service, _ = _service()
    entered = 0
    entered_event = asyncio.Event()
    release_event = asyncio.Event()

    async def build(_intent, _strategy_params, _db):
        nonlocal entered
        entered += 1
        if entered == 2:
            entered_event.set()
        await release_event.wait()
        return "payload", _context_summary()

    monkeypatch.setattr(service_module.ContextBuilder, "build", AsyncMock(side_effect=build))

    bot_id = uuid4()
    config = AdvisorConfig(mode=AdvisorMode.SHADOW, max_concurrent=2)
    tasks = [
        asyncio.create_task(service.review(**_kwargs(bot_id=bot_id, config=config)))
        for _ in range(2)
    ]
    await asyncio.wait_for(entered_event.wait(), timeout=1.0)

    skipped = await service.review(**_kwargs(bot_id=bot_id, config=config))
    release_event.set()
    results = await asyncio.gather(*tasks)

    verdicts = [skipped[0], *(result[0] for result in results)]
    assert any(
        verdict.action == "fail_open" and verdict.reasoning == "advisor_in_flight"
        for verdict in verdicts
    )
