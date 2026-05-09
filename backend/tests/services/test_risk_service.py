"""Phase 10a — RiskService unit tests.

Tests grow incrementally per task: B1 covers EvaluationContext, GateVerdict
returned shape, and the _resolve_limit lookup walk. B2-B7 add per-check
tests; B8 adds the aggregator.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


@pytest.fixture
def evaluation_ctx():
    from app.services.risk_service import EvaluationContext

    return EvaluationContext(
        account_id=uuid.uuid4(),
        broker_id="ibkr",
        instrument_id=42,
        side="buy",
        qty=Decimal("100"),
        price=Decimal("150.0"),
        order_type="LMT",
        time_in_force="DAY",
        request_id="req-test-001",
        currency_base="USD",
    )


def _mock_session_returning(*scalars):
    """Build an AsyncMock AsyncSession whose execute() returns the given scalars
    in order (one per call to scalar_one_or_none)."""
    session = AsyncMock(spec=AsyncSession)
    results = [MagicMock(scalar_one_or_none=MagicMock(return_value=s)) for s in scalars]
    session.execute = AsyncMock(side_effect=results)
    return session


async def test_resolve_limit_walks_account_then_broker_then_global(
    evaluation_ctx,
) -> None:
    """Cap lookup: account → broker → global; first active hit wins."""
    from app.models.risk import RiskLimit
    from app.services.risk_service import RiskService

    acct_limit = RiskLimit(
        id=1,
        scope_type="account",
        scope_id=str(evaluation_ctx.account_id),
        limit_kind="pdt_warn_remaining",
        limit_value=Decimal("3"),
        is_active=True,
        notes="",
        updated_by="op",
    )
    db = _mock_session_returning(acct_limit)
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    got = await svc._resolve_limit(
        evaluation_ctx.account_id, evaluation_ctx.broker_id, "pdt_warn_remaining"
    )
    assert got is not None
    assert got.limit_value == Decimal("3")
    assert db.execute.await_count == 1  # account match wins; broker/global not queried


async def test_resolve_limit_falls_through_to_global(evaluation_ctx) -> None:
    from app.models.risk import RiskLimit
    from app.services.risk_service import RiskService

    global_limit = RiskLimit(
        id=2,
        scope_type="global",
        scope_id=None,
        limit_kind="max_daily_loss_currency_base",
        limit_value=Decimal("1000"),
        is_active=True,
        notes="",
        updated_by="op",
    )
    db = _mock_session_returning(None, None, global_limit)
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    got = await svc._resolve_limit(
        evaluation_ctx.account_id,
        evaluation_ctx.broker_id,
        "max_daily_loss_currency_base",
    )
    assert got is not None
    assert got.scope_type == "global"
    assert db.execute.await_count == 3  # walked all three scopes


async def test_resolve_limit_returns_none_when_no_match(evaluation_ctx) -> None:
    from app.services.risk_service import RiskService

    db = _mock_session_returning(None, None, None)
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    got = await svc._resolve_limit(
        evaluation_ctx.account_id,
        evaluation_ctx.broker_id,
        "max_daily_loss_currency_base",
    )
    assert got is None


async def test_evaluate_returns_gate_verdict_shape(evaluation_ctx) -> None:
    """Skeleton evaluate returns ALLOW with empty blockers/warnings (B1).

    Subsequent tasks (B2-B8) replace this with the real aggregator.
    """
    from app.schemas.risk import GateVerdict
    from app.services.risk_service import RiskService

    db = _mock_session_returning()  # not consulted in skeleton
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert isinstance(verdict, GateVerdict)
    assert verdict.final_verdict == "allow"
    assert verdict.blockers == []
    assert verdict.warnings == []
    assert verdict.latency_ms >= 0


# ─── B2: account + broker kill switch ───────────────────────────────────


async def test_account_kill_switch_off_allows(evaluation_ctx) -> None:
    from app.services.risk_service import RiskService

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=MagicMock(is_enabled=False, reason=""))
        )
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_account_kill_switch(evaluation_ctx)
    assert res is None  # ALLOW = no blocker/warning


async def test_account_kill_switch_off_when_no_row(evaluation_ctx) -> None:
    """Account never frozen → no row in account_kill_switches → ALLOW."""
    from app.services.risk_service import RiskService

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_account_kill_switch(evaluation_ctx)
    assert res is None


async def test_account_kill_switch_on_blocks(evaluation_ctx) -> None:
    from app.services.risk_service import RiskService

    db = AsyncMock(spec=AsyncSession)
    row = MagicMock(is_enabled=True, reason="risk freeze", account_id=evaluation_ctx.account_id)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=row)))
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_account_kill_switch(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.code == "account_kill_switch_enabled"
    assert "risk freeze" in blocker.message


async def test_broker_kill_switch_off_allows(evaluation_ctx) -> None:
    from app.services.risk_service import RiskService

    config = AsyncMock()
    config.get_bool = AsyncMock(return_value=False)
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=config, sidecar=AsyncMock())
    res = await svc._check_broker_kill_switch(evaluation_ctx)
    assert res is None
    config.get_bool.assert_awaited_once_with("broker", "kill_switch_enabled", default=False)


async def test_broker_kill_switch_on_blocks(evaluation_ctx) -> None:
    """Composes Phase 5b H0: app_config.broker.kill_switch_enabled=True."""
    from app.services.risk_service import RiskService

    config = AsyncMock()
    config.get_bool = AsyncMock(return_value=True)
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=config, sidecar=AsyncMock())
    res = await svc._check_broker_kill_switch(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.code == "broker_kill_switch_enabled"
    assert "ibkr" in blocker.message  # broker_id from evaluation_ctx
