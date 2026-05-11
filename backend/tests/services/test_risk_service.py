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
    """B1 shape contract preserved by B8 aggregator.

    With bare AsyncMock deps every check raises (no .scalar_one_or_none /
    no .get_account_summary) → fail-CLOSED to evaluator_error blockers,
    but the GateVerdict shape (final_verdict, blockers, warnings,
    latency_ms) is the contract this test guards.
    """
    from app.schemas.risk import GateVerdict
    from app.services.risk_service import RiskService

    db = _mock_session_returning()  # exhausted side_effect → checks raise
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert isinstance(verdict, GateVerdict)
    assert verdict.final_verdict in ("allow", "warn", "block")
    assert isinstance(verdict.blockers, list)
    assert isinstance(verdict.warnings, list)
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
    # D9-fix: reason text no longer interpolated into the JSONB-bound
    # message (PII leak risk); UI fetches live reason from the admin
    # kill-switch GET endpoint instead.
    assert blocker.message == "account kill switch enabled"


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
    *,
    cap_row: object | None,
    view_row: tuple[Decimal, Decimal] | None,
    staleness_s: float = 0.0,
) -> AsyncMock:
    """Build a session whose execute() yields cap-resolver result then view result.

    When cap_row is None, the resolver walks all 3 scopes (account → broker →
    global), so execute is called 3x and the view is never queried.

    Phase 10a.5 A3.1: the view now returns (realized, unrealized, staleness_s).
    Tests pass realized + unrealized as ``view_row``; ``staleness_s`` defaults
    to 0.0 (fresh) and can be overridden to drive the staleness-WARN branch.
    The mock row exposes ``staleness_s`` as an attribute AND keeps tuple
    indexing for ``row[0]`` / ``row[1]`` reads in the service.
    """
    session = AsyncMock(spec=AsyncSession)
    if cap_row is None:
        none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        session.execute = AsyncMock(side_effect=[none_result, none_result, none_result])
        return session
    cap_result = MagicMock(scalar_one_or_none=MagicMock(return_value=cap_row))
    if view_row is None:
        view_row_obj: object | None = None
    else:
        realized, unrealized = view_row
        tup = (realized, unrealized, staleness_s)
        view_row_obj = MagicMock()
        view_row_obj.__getitem__ = lambda self, idx: tup[idx]
        view_row_obj.staleness_s = staleness_s
    view_result = MagicMock(first=MagicMock(return_value=view_row_obj))
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


async def test_max_daily_loss_missing_row_warns_stale(evaluation_ctx) -> None:
    """Phase 10a.5 A3.1 (CRIT-2): no row in v_account_intraday_pnl → WARN, not ALLOW."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=None,
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "max_daily_loss_pnl_stale"
    assert "stale" in warning.message.lower() or "absent" in warning.message.lower()


async def test_max_daily_loss_stale_row_warns(evaluation_ctx) -> None:
    """Phase 10a.5 A3.1 (CRIT-2): staleness > 90s → WARN (not silent ALLOW)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=(Decimal("-500"), Decimal("-200")),
        staleness_s=120.0,  # > 90s threshold (3x the 30s discoverer cycle)
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "max_daily_loss_pnl_stale"


async def test_max_daily_loss_fresh_row_evaluates_normally(evaluation_ctx) -> None:
    """Phase 10a.5 A3.1: staleness <= 90s does NOT short-circuit; cap still evaluated."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_max_loss(
        cap_row=_max_loss_limit(value="1000", warn_at_pct="80"),
        view_row=(Decimal("-1500"), Decimal("0")),
        staleness_s=45.0,  # within freshness window
    )
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_max_daily_loss(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.check == "max_daily_loss"
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


# ─── B5: position concentration (cross-broker by instrument_id) ─────────


def _concentration_limit(*, value: str, warn_at_pct: str | None) -> object:
    """Build a RiskLimit row for ``max_position_concentration_pct`` cap kind."""
    from app.models.risk import RiskLimit

    return RiskLimit(
        id=30,
        scope_type="account",
        scope_id="acct-id",
        limit_kind="max_position_concentration_pct",
        limit_value=Decimal(value),
        warn_at_pct=Decimal(warn_at_pct) if warn_at_pct is not None else None,
        is_active=True,
        notes="",
        updated_by="op",
    )


def _mock_session_for_concentration(
    *, cap_row: object | None, sum_value: Decimal | None
) -> AsyncMock:
    """Cap-resolver result then SUM(market_value_base) result.

    ``sum_value=None`` is interpreted as the COALESCE returning Decimal("0"),
    which is what Postgres returns when no positions exist for the instrument.
    """
    session = AsyncMock(spec=AsyncSession)
    if cap_row is None:
        none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        session.execute = AsyncMock(side_effect=[none_result, none_result, none_result])
        return session
    cap_result = MagicMock(scalar_one_or_none=MagicMock(return_value=cap_row))
    # Note: positions SUM uses .scalar() (single-row aggregate via COALESCE),
    # NOT .scalar_one_or_none() like the cap-resolver lookups. Mock both
    # accessors so a future change to the production call site doesn't
    # silently pass while breaking in production.
    sum_result = MagicMock(
        scalar=MagicMock(return_value=sum_value or Decimal("0")),
        scalar_one_or_none=MagicMock(return_value=sum_value or Decimal("0")),
    )
    session.execute = AsyncMock(side_effect=[cap_result, sum_result])
    return session


def _sidecar_with_nlv(nlv: str) -> AsyncMock:
    sidecar = AsyncMock()
    sidecar.get_account_summary = AsyncMock(return_value=MagicMock(nlv_currency_base=nlv))
    return sidecar


async def test_concentration_no_instrument_id_skips(evaluation_ctx) -> None:
    """``ctx.instrument_id is None`` (e.g. cash trade) → no check → ALLOW."""
    from app.services.risk_service import EvaluationContext, RiskService

    ctx_no_inst = EvaluationContext(
        account_id=evaluation_ctx.account_id,
        broker_id=evaluation_ctx.broker_id,
        instrument_id=None,
        side=evaluation_ctx.side,
        qty=evaluation_ctx.qty,
        price=evaluation_ctx.price,
        order_type=evaluation_ctx.order_type,
        time_in_force=evaluation_ctx.time_in_force,
        request_id=evaluation_ctx.request_id,
        currency_base=evaluation_ctx.currency_base,
    )
    db = AsyncMock(spec=AsyncSession)  # never consulted
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=AsyncMock())
    assert await svc._check_position_concentration(ctx_no_inst) is None
    db.execute.assert_not_awaited()


async def test_concentration_no_cap_allows(evaluation_ctx) -> None:
    """No active cap at any scope → ALLOW; sidecar never queried."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_concentration(cap_row=None, sum_value=None)
    sidecar = AsyncMock()
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_position_concentration(evaluation_ctx) is None
    sidecar.get_account_summary.assert_not_awaited()
    assert db.execute.await_count == 3  # walked all 3 scopes; positions never queried


async def test_concentration_under_cap_allows(evaluation_ctx) -> None:
    """Buy 100 @ 150 = 15 000 added to current 5 000 = 20 000 / 200 000 NLV
    = 10% < cap 25% (warn at 80% of cap = 20%) → ALLOW."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_concentration(
        cap_row=_concentration_limit(value="25", warn_at_pct="80"),
        sum_value=Decimal("5000"),
    )
    sidecar = _sidecar_with_nlv("200000")
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_position_concentration(evaluation_ctx) is None


async def test_concentration_at_warn_warns(evaluation_ctx) -> None:
    """current 30 000 + buy 15 000 = 45 000 / 200 000 = 22.5%; cap 25%, warn 80% (=20%) → WARN."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_concentration(
        cap_row=_concentration_limit(value="25", warn_at_pct="80"),
        sum_value=Decimal("30000"),
    )
    sidecar = _sidecar_with_nlv("200000")
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_position_concentration(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "position_concentration"
    assert warning.value == 22.5
    assert warning.threshold == 25.0


async def test_concentration_over_cap_blocks(evaluation_ctx) -> None:
    """current 50 000 + buy 15 000 = 65 000 / 200 000 = 32.5%; cap 25% → BLOCK."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_concentration(
        cap_row=_concentration_limit(value="25", warn_at_pct="80"),
        sum_value=Decimal("50000"),
    )
    sidecar = _sidecar_with_nlv("200000")
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_position_concentration(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.check == "position_concentration"
    assert blocker.code == "position_concentration_exceeded"


async def test_concentration_sql_aggregates_cross_broker(evaluation_ctx) -> None:
    """Documents H2 invariant: SQL must NOT filter by account_id.

    Concrete behavior assertion: the parameterised SQL submitted to execute()
    contains ``WHERE instrument_id`` and does NOT contain ``account_id``. This
    catches accidental same-broker scoping in future refactors.
    """
    from app.services.risk_service import RiskService

    db = _mock_session_for_concentration(
        cap_row=_concentration_limit(value="50", warn_at_pct=None),
        sum_value=Decimal("0"),
    )
    sidecar = _sidecar_with_nlv("200000")
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    await svc._check_position_concentration(evaluation_ctx)
    # The 2nd execute() call is the positions SUM; inspect its `text()` payload.
    positions_call = db.execute.await_args_list[1]
    sql_text = str(positions_call.args[0])
    assert "instrument_id" in sql_text
    assert "account_id" not in sql_text  # cross-broker H2 invariant


# ─── B6: buying-power buffer with in-flight commitments ──────────────────


def _bp_buffer_limit(*, value: str) -> object:
    """Build a RiskLimit row for ``min_buying_power_buffer_pct`` cap kind.

    Spec §1 #6 + §3 ENUM: ``limit_value`` is the *required headroom %* below
    which the gate WARNs (e.g. 10 = require 10% of effective_bp left over
    after the trade). BLOCK is unconditional when notional exceeds BP.
    """
    from app.models.risk import RiskLimit

    return RiskLimit(
        id=40,
        scope_type="account",
        scope_id="acct-id",
        limit_kind="min_buying_power_buffer_pct",
        limit_value=Decimal(value),
        warn_at_pct=None,
        is_active=True,
        notes="",
        updated_by="op",
    )


def _mock_session_for_bp(*, cap_row: object | None) -> AsyncMock:
    """Session whose execute() yields the cap-resolver result; no other DB calls."""
    session = AsyncMock(spec=AsyncSession)
    if cap_row is None:
        none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        session.execute = AsyncMock(side_effect=[none_result, none_result, none_result])
        return session
    cap_result = MagicMock(scalar_one_or_none=MagicMock(return_value=cap_row))
    session.execute = AsyncMock(side_effect=[cap_result])
    return session


def _sidecar_with_bp(bp: str) -> AsyncMock:
    sidecar = AsyncMock()
    sidecar.get_account_summary = AsyncMock(return_value=MagicMock(buying_power=bp))
    return sidecar


def _redis_with_bp_committed(committed: str | None) -> AsyncMock:
    """Build an AsyncMock Redis whose .get returns the inflight BP value (or None)."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=committed)
    return redis


async def test_bp_no_cap_allows(evaluation_ctx) -> None:
    """No active ``min_buying_power_buffer_pct`` cap → ALLOW; sidecar untouched."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=None)
    sidecar = AsyncMock()
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_buying_power(evaluation_ctx) is None
    sidecar.get_account_summary.assert_not_awaited()
    assert db.execute.await_count == 3


async def test_bp_sufficient_no_warn_allows(evaluation_ctx) -> None:
    """notional 15 000, bp 100 000, committed 0 → effective 100 000;
    remaining 85 000 ≥ buffer (10% of 100 000 = 10 000) → ALLOW."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=_bp_buffer_limit(value="10"))
    sidecar = _sidecar_with_bp("100000")
    redis = _redis_with_bp_committed(None)
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_buying_power(evaluation_ctx) is None


async def test_bp_buffer_warn(evaluation_ctx) -> None:
    """notional 15 000, bp 20 000, committed 0; remaining 5 000 < 50% buffer (10 000) → WARN."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=_bp_buffer_limit(value="50"))
    sidecar = _sidecar_with_bp("20000")
    redis = _redis_with_bp_committed(None)
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_buying_power(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "buying_power"
    assert warning.value == 5000.0
    assert warning.threshold == 10000.0


async def test_bp_insufficient_blocks(evaluation_ctx) -> None:
    """notional 15 000 > effective_bp 10 000 → BLOCK."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=_bp_buffer_limit(value="10"))
    sidecar = _sidecar_with_bp("10000")
    redis = _redis_with_bp_committed(None)
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_buying_power(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.code == "buying_power_insufficient"


async def test_bp_inflight_gobbles_blocks(evaluation_ctx) -> None:
    """bp 50 000 but committed 40 000 → effective 10 000 < notional 15 000 → BLOCK.

    Demonstrates H3: in-flight commitments must subtract from BP **before**
    the buffer check so a fast double-buy can't both be approved.
    """
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=_bp_buffer_limit(value="10"))
    sidecar = _sidecar_with_bp("50000")
    redis = _redis_with_bp_committed("40000")
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_buying_power(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.code == "buying_power_insufficient"


async def test_bp_sell_skips(evaluation_ctx) -> None:
    """Sell orders reduce BP usage → check inapplicable → ALLOW."""
    from app.services.risk_service import EvaluationContext, RiskService

    sell_ctx = EvaluationContext(
        account_id=evaluation_ctx.account_id,
        broker_id=evaluation_ctx.broker_id,
        instrument_id=evaluation_ctx.instrument_id,
        side="sell",
        qty=evaluation_ctx.qty,
        price=evaluation_ctx.price,
        order_type=evaluation_ctx.order_type,
        time_in_force=evaluation_ctx.time_in_force,
        request_id=evaluation_ctx.request_id,
        currency_base=evaluation_ctx.currency_base,
    )
    db = AsyncMock(spec=AsyncSession)
    sidecar = AsyncMock()
    svc = RiskService(db=db, redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_buying_power(sell_ctx) is None
    db.execute.assert_not_awaited()
    sidecar.get_account_summary.assert_not_awaited()


# ─── B7: sidecar margin preview (asymmetric preview vs place_order) ─────


def _grpc_err_unimplemented() -> Exception:
    """Build a real grpc.aio.AioRpcError with code UNIMPLEMENTED.

    AioRpcError is a real exception class but its constructor demands
    Status + initial_metadata + trailing_metadata. We subclass it and
    stub the .code() / .details() methods so the gate's exception filter
    sees a true AioRpcError without us having to spin up a real RPC.
    """
    import grpc

    class _StubAioRpcError(grpc.aio.AioRpcError):  # type: ignore[misc]
        def __init__(self) -> None:
            # Skip parent __init__ — we only need .code()/.details() to work.
            pass

        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.UNIMPLEMENTED

        def details(self) -> str:
            return "alpaca preview unimplemented"

    return _StubAioRpcError()


async def test_margin_preview_accepted_allows(evaluation_ctx) -> None:
    """Sidecar returns accepted=True; both modes ALLOW."""
    from app.services.risk_service import RiskService

    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(
        return_value=MagicMock(accepted=True, reject_reason="", initial_margin="500")
    )
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    assert await svc._check_margin(evaluation_ctx, mode="preview") is None
    assert await svc._check_margin(evaluation_ctx, mode="place_order") is None


async def test_margin_preview_timeout_warns(evaluation_ctx) -> None:
    """Preview mode + timeout > 500ms → WARN (do not block UX)."""
    import asyncio

    from app.services.risk_service import RiskService

    async def slow_preview(**kwargs: object) -> object:
        await asyncio.sleep(5)  # well past 0.5s preview deadline
        return MagicMock(accepted=True)

    sidecar = AsyncMock()
    sidecar.preview_order = slow_preview  # type: ignore[method-assign]
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    res = await svc._check_margin(evaluation_ctx, mode="preview")
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "margin"
    assert "pending" in warning.message  # H4 preview soft-fail surfaces pending state


async def test_margin_place_order_timeout_blocks(evaluation_ctx) -> None:
    """place_order timeout (>3s) → BLOCK with margin_check_unavailable (H4 fail-CLOSED)."""
    import asyncio

    from app.services.risk_service import RiskService

    async def slow_preview(**kwargs: object) -> object:
        await asyncio.sleep(5)
        return MagicMock(accepted=True)

    sidecar = AsyncMock()
    sidecar.preview_order = slow_preview  # type: ignore[method-assign]
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    # Patch asyncio.wait_for to raise immediately to avoid 3s test latency.
    import unittest.mock

    with unittest.mock.patch(
        "app.services.risk_service.asyncio.wait_for",
        side_effect=TimeoutError(),
    ):
        res = await svc._check_margin(evaluation_ctx, mode="place_order")
    assert res is not None
    blocker, warning = res
    assert warning is None
    assert blocker is not None
    assert blocker.check == "margin"
    assert blocker.code == "margin_check_unavailable"


async def test_margin_unimplemented_warns_either_mode(evaluation_ctx) -> None:
    """Sidecar UNIMPLEMENTED (Alpaca) → WARN regardless of mode."""
    from app.services.risk_service import RiskService

    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(side_effect=_grpc_err_unimplemented())
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    for mode in ("preview", "place_order"):
        res = await svc._check_margin(evaluation_ctx, mode=mode)  # type: ignore[arg-type]
        assert res is not None
        blocker, warning = res
        assert blocker is None
        assert warning is not None
        assert warning.check == "margin"
        assert "unavailable" in warning.message.lower()


async def test_margin_non_unimplemented_grpc_raises(evaluation_ctx) -> None:
    """gRPC error other than UNIMPLEMENTED (e.g. UNAVAILABLE) is re-raised by
    _check_margin so the aggregator can convert it to evaluator_error."""
    import grpc

    from app.services.risk_service import RiskService

    class _UnavailableErr(grpc.aio.AioRpcError):  # type: ignore[misc]
        def __init__(self) -> None:
            # Skip parent __init__ (demands real Status objects); stub the
            # accessors the gate code touches.
            pass

        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.UNAVAILABLE

        def details(self) -> str:
            return "broker connection lost"

        def __str__(self) -> str:
            return "AioRpcError(UNAVAILABLE): broker connection lost"

    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(side_effect=_UnavailableErr())
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    with pytest.raises(grpc.aio.AioRpcError):
        await svc._check_margin(evaluation_ctx, mode="preview")


async def test_evaluate_non_unimplemented_grpc_becomes_evaluator_error(evaluation_ctx) -> None:
    """The aggregator catches re-raised gRPC errors via except BaseException."""
    import grpc

    from app.services.risk_service import RiskService

    class _UnavailableErr(grpc.aio.AioRpcError):  # type: ignore[misc]
        def __init__(self) -> None:
            # Skip parent __init__ (demands real Status objects); stub the
            # accessors the gate code touches.
            pass

        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.UNAVAILABLE

        def details(self) -> str:
            return "broker connection lost"

        def __str__(self) -> str:
            return "AioRpcError(UNAVAILABLE): broker connection lost"

    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(side_effect=_UnavailableErr())
    sidecar.get_account_summary = AsyncMock(
        return_value=MagicMock(buying_power="100000", nlv_currency_base="200000")
    )
    svc = RiskService(
        db=_all_checks_allow_session(),
        redis=AsyncMock(),
        config=AsyncMock(get_bool=AsyncMock(return_value=False)),
        sidecar=sidecar,
    )
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert verdict.final_verdict == "block"
    assert any(b.code == "evaluator_error" for b in verdict.blockers)
    assert any("AioRpcError" in b.message or "Unavailable" in b.message for b in verdict.blockers)


# ─── B9 reviewer findings: PDT/BP Redis-error WARN paths ────────────────


async def test_pdt_redis_unreachable_warns(evaluation_ctx) -> None:
    """Spec §4: Redis ConnectionError -> WARN, not BLOCK (operational hiccup)."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_pdt(cap_row=_pdt_warn_limit(warn_remaining="2"))
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=AsyncMock())
    res = await svc._check_pdt(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "pdt"
    assert "degraded" in warning.message.lower()


async def test_bp_redis_unreachable_warns(evaluation_ctx) -> None:
    """Spec §4: Redis ConnectionError -> WARN, not BLOCK."""
    from app.services.risk_service import RiskService

    db = _mock_session_for_bp(cap_row=_bp_buffer_limit(value="10"))
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=OSError("connection reset"))
    svc = RiskService(db=db, redis=redis, config=AsyncMock(), sidecar=_sidecar_with_bp("100000"))
    res = await svc._check_buying_power(evaluation_ctx)
    assert res is not None
    blocker, warning = res
    assert blocker is None
    assert warning is not None
    assert warning.check == "buying_power"
    assert "degraded" in warning.message.lower()


# ─── B8: evaluate() aggregator (verdict precedence) ─────────────────────


def _all_checks_allow_session() -> AsyncMock:
    """Build a session that returns None for every cap-resolver lookup so all
    `_resolve_limit` calls miss; combined with a sidecar that accepts the margin
    preview, this drives evaluate() to ALLOW.
    """
    session = AsyncMock(spec=AsyncSession)
    none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    # Generous side_effect: each check resolves up to 3 scopes; padding for kill switch + view.
    session.execute = AsyncMock(side_effect=[none_result] * 30)
    return session


def _allowing_sidecar() -> AsyncMock:
    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(
        return_value=MagicMock(accepted=True, reject_reason="", initial_margin="100")
    )
    sidecar.get_account_summary = AsyncMock(
        return_value=MagicMock(buying_power="100000", nlv_currency_base="200000")
    )
    return sidecar


async def test_evaluate_all_allow_returns_allow_verdict(evaluation_ctx) -> None:
    """No caps configured + sidecar accepts → ALLOW with empty blockers/warnings."""
    from app.schemas.risk import GateVerdict
    from app.services.risk_service import RiskService

    svc = RiskService(
        db=_all_checks_allow_session(),
        redis=AsyncMock(),
        config=AsyncMock(get_bool=AsyncMock(return_value=False)),
        sidecar=_allowing_sidecar(),
    )
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert isinstance(verdict, GateVerdict)
    assert verdict.final_verdict == "allow"
    assert verdict.blockers == []
    assert verdict.warnings == []
    assert verdict.latency_ms >= 0


async def test_evaluate_account_kill_switch_blocks(evaluation_ctx) -> None:
    """Single blocker (kill switch) → BLOCK regardless of other checks."""
    from app.services.risk_service import RiskService

    db = AsyncMock(spec=AsyncSession)
    enabled_row = MagicMock(is_enabled=True, reason="manual freeze")
    none_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    enabled_result = MagicMock(scalar_one_or_none=MagicMock(return_value=enabled_row))
    # First execute is the kill-switch lookup; rest are no-cap.
    db.execute = AsyncMock(side_effect=[enabled_result] + [none_result] * 30)
    svc = RiskService(
        db=db,
        redis=AsyncMock(),
        config=AsyncMock(get_bool=AsyncMock(return_value=False)),
        sidecar=_allowing_sidecar(),
    )
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert verdict.final_verdict == "block"
    assert any(b.code == "account_kill_switch_enabled" for b in verdict.blockers)


async def test_evaluate_unhandled_exception_becomes_evaluator_error_block(
    evaluation_ctx,
) -> None:
    """An unexpected exception in any check → fail-CLOSED with evaluator_error blocker."""
    from app.services.risk_service import RiskService

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(side_effect=RuntimeError("DB exploded"))
    svc = RiskService(
        db=db,
        redis=AsyncMock(),
        config=AsyncMock(get_bool=AsyncMock(return_value=False)),
        sidecar=_allowing_sidecar(),
    )
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert verdict.final_verdict == "block"
    assert any(b.code == "evaluator_error" for b in verdict.blockers)
    # The error message names the exception type so operators can triage.
    assert any("RuntimeError" in b.message for b in verdict.blockers)


async def test_evaluate_warning_only_returns_warn_verdict(evaluation_ctx) -> None:
    """A single warning with no blockers → WARN (precedence: block > warn > allow)."""
    from app.services.risk_service import RiskService

    db = _all_checks_allow_session()
    sidecar = AsyncMock()
    # Sidecar preview times out → preview-mode WARN from _check_margin.
    import asyncio

    async def slow_preview(**kwargs: object) -> object:
        await asyncio.sleep(5)
        return MagicMock(accepted=True)

    sidecar.preview_order = slow_preview  # type: ignore[method-assign]
    sidecar.get_account_summary = AsyncMock(
        return_value=MagicMock(buying_power="100000", nlv_currency_base="200000")
    )
    svc = RiskService(
        db=db,
        redis=AsyncMock(),
        config=AsyncMock(get_bool=AsyncMock(return_value=False)),
        sidecar=sidecar,
    )
    verdict = await svc.evaluate(evaluation_ctx, mode="preview")
    assert verdict.final_verdict == "warn"
    assert any(w.check == "margin" for w in verdict.warnings)
    assert verdict.blockers == []


async def test_margin_rejected_by_broker_blocks(evaluation_ctx) -> None:
    """accepted=False with a reject_reason → BLOCK both modes."""
    from app.services.risk_service import RiskService

    sidecar = AsyncMock()
    sidecar.preview_order = AsyncMock(
        return_value=MagicMock(
            accepted=False, reject_reason="insufficient maintenance margin", initial_margin=None
        )
    )
    svc = RiskService(db=AsyncMock(), redis=AsyncMock(), config=AsyncMock(), sidecar=sidecar)
    for mode in ("preview", "place_order"):
        res = await svc._check_margin(evaluation_ctx, mode=mode)  # type: ignore[arg-type]
        assert res is not None
        blocker, warning = res
        assert warning is None
        assert blocker is not None
        assert blocker.code == "margin_rejected_by_broker"
        assert "insufficient maintenance margin" in blocker.message
