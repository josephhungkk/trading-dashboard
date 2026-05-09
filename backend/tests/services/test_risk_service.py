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
