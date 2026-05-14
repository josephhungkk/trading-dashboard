"""Tests for Phase 12 options risk checks."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_ctx(
    *,
    asset_class="OPTION",
    side="sell",
    position_effect="OPEN",
    multiplier=100,
    symbol="SPY250117C00450000",
    instrument_id=42,
):
    from app.services.risk_service import EvaluationContext

    return EvaluationContext(
        account_id=uuid.uuid4(),
        broker_id="ibkr",
        instrument_id=instrument_id,
        side=side,
        qty=Decimal("1"),
        price=Decimal("5.00"),
        order_type="LIMIT",
        time_in_force="DAY",
        request_id="test-001",
        currency_base="USD",
        symbol=symbol,
        asset_class=asset_class,
        multiplier=multiplier,
        position_effect=position_effect,
    )


def test_evaluation_context_has_multiplier():
    ctx = _make_ctx(multiplier=100)
    assert ctx.multiplier == 100


def test_evaluation_context_has_position_effect():
    ctx = _make_ctx(position_effect="OPEN")
    assert ctx.position_effect == "OPEN"


def test_evaluation_context_multiplier_defaults_to_1():
    from app.services.risk_service import EvaluationContext

    ctx = EvaluationContext(
        account_id=uuid.uuid4(),
        broker_id="ibkr",
        instrument_id=None,
        side="buy",
        qty=Decimal("100"),
        price=Decimal("150"),
        order_type="LIMIT",
        time_in_force="DAY",
        request_id="test-002",
        currency_base="USD",
    )
    assert ctx.multiplier == 1
    assert ctx.position_effect is None


def _make_risk_service(*, config_values=None):
    from app.services.risk_service import RiskService

    config_values = config_values or {}
    db = AsyncMock()
    redis = AsyncMock()

    async def get_bool(ns, key, *, default=False):
        return config_values.get(f"{ns}/{key}", default)

    async def get_int(ns, key, *, default=None):
        return config_values.get(f"{ns}/{key}", default)

    async def get_json(ns, key, default=None):
        return config_values.get(f"{ns}/{key}", default)

    cfg = MagicMock()
    cfg.get_bool = get_bool
    cfg.get_int = get_int
    cfg.get_json = get_json

    svc = RiskService(db=db, redis=redis, config=cfg, sidecar=MagicMock())
    return svc


@pytest.mark.asyncio
async def test_options_level_gate_blocks_when_level_too_low():
    """STO naked call is blocked at trading level 1 (no cover)."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="sell", position_effect="OPEN")

    svc._get_existing_long_position = AsyncMock(return_value=Decimal("0"))
    svc._get_option_expiry = AsyncMock(return_value=None)

    result = await svc._check_options_exposure(ctx)
    assert result is not None
    blocker, warning = result
    assert blocker is not None
    assert blocker.code == "naked_short_not_permitted"
    assert warning is None


@pytest.mark.asyncio
async def test_bto_always_allowed_at_l1():
    """BTO (long call/put) passes at trading level 1 — no expiry."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    svc._get_option_expiry = AsyncMock(return_value=None)

    result = await svc._check_options_exposure(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_expiry_cutoff_blocks_open_order():
    """Opening an order after cutoff on expiry day is BLOCK."""
    import datetime

    svc = _make_risk_service()
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    expiry = datetime.date(2025, 1, 17)
    svc._get_option_expiry = AsyncMock(return_value=expiry)
    svc._get_instrument_exchange = AsyncMock(return_value="NYSE")

    with patch("app.services.market_calendar") as mc:
        mc.today_in_exchange_tz.return_value = datetime.date(2025, 1, 18)  # strictly past

        result = await svc._check_options_exposure(ctx)
        assert result is not None
        blocker, warning = result
        assert blocker is not None
        assert blocker.code == "option_cutoff_passed"
        assert warning is None


@pytest.mark.asyncio
async def test_zero_dte_warn():
    """0DTE order produces a WARN when today == expiry (exchange-local)."""
    import datetime

    svc = _make_risk_service(config_values={"options/trading_level": 4})
    ctx = _make_ctx(side="buy", position_effect="OPEN")

    expiry = datetime.date(2025, 1, 17)
    svc._get_option_expiry = AsyncMock(return_value=expiry)
    svc._get_instrument_exchange = AsyncMock(return_value="NYSE")

    with patch("app.services.market_calendar") as mc:
        mc.today_in_exchange_tz.return_value = expiry  # today == expiry → 0DTE

        result = await svc._check_options_exposure(ctx)
        assert result is not None
        blocker, warning = result
        assert blocker is None
        assert warning is not None
        assert warning.check == "options_exposure"
        assert "0DTE" in warning.message


@pytest.mark.asyncio
async def test_stc_allowed_at_l1():
    """STC (sell-to-close) always passes even at level 1 — not a naked short."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="sell", position_effect="CLOSE")

    svc._get_option_expiry = AsyncMock(return_value=None)
    svc._get_instrument_exchange = AsyncMock(return_value=None)

    result = await svc._check_options_exposure(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_sto_with_cover_allowed_at_l1():
    """STO with existing cover (covered call) passes at level 1."""
    svc = _make_risk_service(config_values={"options/trading_level": 1})
    ctx = _make_ctx(side="sell", position_effect="OPEN")

    svc._get_existing_long_position = AsyncMock(return_value=Decimal("5"))
    svc._get_option_expiry = AsyncMock(return_value=None)
    svc._get_instrument_exchange = AsyncMock(return_value=None)

    result = await svc._check_options_exposure(ctx)
    assert result is None
