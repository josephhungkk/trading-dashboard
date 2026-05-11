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


# ─── B3: max-daily-loss (realized + unrealized intraday P&L) ────────────


def _mock_session_for_max_loss(
    *, cap_row: object | None, view_row: tuple[Decimal, Decimal] | None
) -> AsyncMock:
    """Build a session whose execute() yields cap-resolver result then view result.

    When cap_row is None, the resolver walks all 3 scopes (account → broker →
    global), so execute is called 3x and the view is never queried.
    """
    session = AsyncMock(spec=AsyncSession)
    if cap_row is None:
        none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        session.execute = AsyncMock(side_effect=[none_result, none_result, none_result])
        return session
    cap_result = MagicMock(scalar_one_or_none=MagicMock(return_value=cap_row))
    view_result = MagicMock(first=MagicMock(return_value=view_row))
    session.execute = AsyncMock(side_effect=[cap_result, view_result])
    return session


def _max_loss_limit(*, value: str, warn_at_pct: str | None) -> object:
    """Build a RiskLimit-shaped object the check needs (limit_value, warn_at_pct)."""
    from app.models.risk import RiskLimit

    return RiskLimit(
        id=10,
        scope_type="account",
        scope_id="acct-id",
        limit_kind="max_daily_loss_currency_base",
        limit_value=Decimal(value),
        warn_at_pct=Decimal(warn_at_pct) if warn_at_pct is not None else None,
        is_active=True,
        notes="",
        updated_by="op",
    )


async def test_max_daily_loss_no_cap_allows(evaluation_ctx) -> None:
    """No active cap at any scope → no risk to evaluate → ALLOW (None)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(cap_row=None, view_row=None)
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is None
    # cap resolver walked all 3 scopes; view never queried
    assert db.execute.await_count == 3


async def test_max_daily_loss_under_cap_allows(evaluation_ctx) -> None:
    """realized=-100, unrealized=-50, cap=1000, warn=80% → loss=150 (15%) → ALLOW."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=(Decimal("-100"), Decimal("-50")),
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is None
    assert db.execute.await_count == 2  # cap resolver hit on first scope + view


async def test_max_daily_loss_at_warn_pct_warns(evaluation_ctx) -> None:
    """cap=1000, warn=80%, realized=-800, unrealized=0 → loss=800 (80%) → WARN."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=(Decimal("-800"), Decimal("0")),
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "max_daily_loss"
    assert warning.value == 800.0
    assert warning.threshold == 1000.0
    assert "80" in warning.message  # warn_at_pct surfaces in operator-facing text


async def test_max_daily_loss_over_cap_blocks(evaluation_ctx) -> None:
    """cap=1000, realized=-1500, unrealized=0 → loss=1500 → BLOCK."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=(Decimal("-1500"), Decimal("0")),
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.check == "max_daily_loss"
    assert blocker.code == "max_daily_loss_exceeded"
    assert "1500" in blocker.message


async def test_max_daily_loss_realized_plus_unrealized_blocks(evaluation_ctx) -> None:
    """cap=1000, realized=-600, unrealized=-500 → composed loss=1100 → BLOCK."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct=None),
        view_row=(Decimal("-600"), Decimal("-500")),
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.code == "max_daily_loss_exceeded"


# ─── B4: PDT (broker-reported + in-flight Redis counter) ────────────────


def _pdt_warn_limit(*, warn_remaining: str) -> object:
    """Build a RiskLimit row for ``pdt_warn_remaining`` cap kind.

    Spec §1 #4 + §3 ENUM: ``pdt_warn_remaining.limit_value`` is the threshold
    below which the gate WARNs. BLOCK is unconditional at remaining ≤ 0.
    """
    from app.models.risk import RiskLimit

    return RiskLimit(
        id=20,
        scope_type="account",
        scope_id="acct-id",
        limit_kind="pdt_warn_remaining",
        limit_value=Decimal(warn_remaining),
        warn_at_pct=None,
        is_active=True,
        notes="",
        updated_by="op",
    )


def _mock_session_for_pdt(*, cap_row: object | None) -> AsyncMock:
    """Session whose execute() yields the cap-resolver result.

    No DB calls beyond the cap walk; the in-flight counter and broker-reported
    fall-back live on Redis + sidecar respectively.
    """
    session = AsyncMock(spec=AsyncSession)
    if cap_row is None:
        none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        session.execute = AsyncMock(side_effect=[none_result, none_result, none_result])
        return session
    cap_result = MagicMock(scalar_one_or_none=MagicMock(return_value=cap_row))
    session.execute = AsyncMock(side_effect=[cap_result])
    return session


async def test_pdt_no_cap_allows(evaluation_ctx) -> None:
    """No active ``pdt_warn_remaining`` cap → ALLOW (no risk evaluated)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=None)
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_pdt(evaluation_ctx)
    assert res is None
    assert db.execute.await_count == 3  # walked all 3 scopes


async def test_pdt_inflight_counter_remaining_high_allows(evaluation_ctx) -> None:
    """In-flight=5, warn_at=2 → 5 > 2 → ALLOW (no broker fallback)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=_pdt_warn_limit(warn_remaining="2"))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="5")  # inflight set
    sidecar = AsyncMock()  # not consulted
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_pdt(evaluation_ctx)
    assert res is None
    sidecar.get_account_summary.assert_not_awaited()


async def test_pdt_inflight_at_warn_threshold_warns(evaluation_ctx) -> None:
    """In-flight=2, warn_at=2 → remaining ≤ warn → WARN."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=_pdt_warn_limit(warn_remaining="2"))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2")
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_pdt(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "pdt"
    assert warning.value == 2.0
    assert warning.threshold == 2.0


async def test_pdt_inflight_zero_blocks(evaluation_ctx) -> None:
    """In-flight=0 → BLOCK (PDT trade-count exhausted)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=_pdt_warn_limit(warn_remaining="2"))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="0")
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_pdt(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.check == "pdt"
    assert blocker.code == "pdt_exhausted"


async def test_pdt_inflight_unset_falls_back_to_broker_reported(evaluation_ctx) -> None:
    """Counter unset → ``sidecar.get_account_summary`` provides ``dayTradesRemaining``.

    broker_reported=1, warn_at=2 → 1 ≤ 2 → WARN (and not BLOCK because >0).
    """
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=_pdt_warn_limit(warn_remaining="2"))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # inflight not set
    sidecar = AsyncMock()
    sidecar.get_account_summary = AsyncMock(return_value=MagicMock(day_trades_remaining=1))
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_pdt(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "pdt"
    assert warning.value == 1.0
    sidecar.get_account_summary.assert_awaited_once_with(evaluation_ctx.account_id)
