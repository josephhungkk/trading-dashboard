from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.position_sizing_math import (
    compute_fixed_fractional,
    compute_risk_per_trade,
    compute_vol_targeted,
)


def test_fixed_fractional_2pct_of_100k_at_50_is_40_shares() -> None:
    """Spec §4.4 golden vector: 2% of $100k NLV at $50 = 40 shares."""
    qty, notional = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        risk_pct=Decimal("2"),
    )
    assert qty == Decimal("40")
    assert notional == Decimal("2000")


def test_risk_per_trade_1pct_at_1_dollar_stop_is_1000_shares() -> None:
    """Spec §4.4 golden vector: 1% of $100k NLV at $1 stop distance = 1000 shares."""
    qty, notional, risk_per_share = compute_risk_per_trade(
        nlv_base=Decimal("100000"),
        entry_base=Decimal("50"),
        stop_base=Decimal("49"),
        side="buy",
        risk_pct=Decimal("1"),
    )
    assert qty == Decimal("1000")
    assert risk_per_share == Decimal("1")
    assert notional == Decimal("50000")


def test_risk_per_trade_rejects_buy_with_stop_above_entry() -> None:
    with pytest.raises(ValueError, match=r"stop.*below.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("51"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_sell_with_stop_below_entry() -> None:
    with pytest.raises(ValueError, match=r"stop.*above.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("49"),
            side="sell",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_zero_distance() -> None:
    with pytest.raises(ValueError, match=r"zero.distance|entry == stop"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("50"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_vol_targeted_15pct_at_25pct_vol_at_50_is_1200_shares() -> None:
    """Spec §4.4 golden: 15% target vol with 25% asset vol at $50 → 1200 shares.

    qty = (0.15 * 100000) / (0.25 * 50) = 15000 / 12.5 = 1200.
    """
    qty, notional = compute_vol_targeted(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        target_vol_pct=Decimal("15"),
        asset_vol_annualized=Decimal("0.25"),
    )
    assert qty == Decimal("1200")
    assert notional == Decimal("60000")


def test_vol_targeted_rejects_zero_vol() -> None:
    with pytest.raises(ValueError, match="zero_volatility"):
        compute_vol_targeted(
            nlv_base=Decimal("100000"),
            price_base=Decimal("50"),
            target_vol_pct=Decimal("15"),
            asset_vol_annualized=Decimal("0"),
        )


def test_fixed_fractional_floors_not_rounds() -> None:
    """3% of $100k at $33 = 3000/33 = 90.909... → floor to 90."""
    qty, _ = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("33"),
        risk_pct=Decimal("3"),
    )
    assert qty == Decimal("90")


# ── Orchestrator tests ──────────────────────────────────────────────────────


from unittest.mock import AsyncMock, MagicMock  # noqa: E402
from uuid import uuid4  # noqa: E402

import fakeredis.aioredis  # noqa: E402

from app.schemas.risk import GateVerdict  # noqa: E402
from app.schemas.sizing import (  # noqa: E402
    FixedFractionalInputs,
    SizingMethod,
)
from app.services.position_sizing_service import PositionSizingService  # noqa: E402


class _SizingSession:
    """In-memory stub: route SELECTs to canned rows by SQL substring."""

    def __init__(self, account_id, instrument_id):
        self._account_id = account_id
        self._instrument_id = instrument_id

    async def execute(self, stmt, params=None):
        sql = str(stmt)

        class _Mappings:
            def __init__(self, row):
                self._row = row

            def first(self):
                return self._row

        class _Result:
            def __init__(self, row):
                self._row = row

            def mappings(self):
                return _Mappings(self._row)

        if "FROM broker_accounts" in sql:
            return _Result(
                {
                    "id": self._account_id,
                    "gateway_label": "ibkr-paper",
                    "mode": "paper",
                    "currency_base": "USD",
                    "last_nlv": Decimal("100000"),
                    "last_nlv_currency": "USD",
                }
            )
        if "FROM instruments" in sql:
            return _Result(
                {
                    "id": self._instrument_id,
                    "display_name": "AAPL",
                    "currency": "USD",
                }
            )
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.mark.asyncio
async def test_orchestrator_fixed_fractional_happy_path(monkeypatch) -> None:
    """compute() loads NLV, FX-converts, runs math, calls gate, returns result.

    Patches RiskService construction so we capture the EvaluationContext
    fed into the gate without standing up a real risk_service + 7 checks.
    """
    account_id = uuid4()
    instrument_id = 67890

    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    await redis.set("fx:USD:USD", "1.0")

    gate = MagicMock()
    gate.evaluate = AsyncMock(
        return_value=GateVerdict(final_verdict="allow", blockers=[], warnings=[], latency_ms=5)
    )
    # Patch RiskService inside position_sizing_service so the orchestrator
    # gets our mock back regardless of __init__ args.
    monkeypatch.setattr("app.services.position_sizing_service.RiskService", lambda **_: gate)

    registry = MagicMock()
    registry.get_client = AsyncMock(return_value=MagicMock())
    config_svc = MagicMock()
    vol_service = MagicMock()

    svc = PositionSizingService(
        db=_SizingSession(account_id, instrument_id),
        redis=redis,
        config=config_svc,
        broker_registry=registry,
        vol_service=vol_service,
    )
    result = await svc.compute(
        account_id=account_id,
        instrument_id=instrument_id,
        method=SizingMethod.fixed_fractional,
        inputs=FixedFractionalInputs(risk_pct=Decimal("2"), price=Decimal("50")),
        side="buy",
    )

    assert result.suggested_qty == Decimal("40")
    assert result.base_currency_notional == Decimal("2000")
    assert result.risk_verdict.final_verdict == "allow"
    assert result.breakdown.fx_rate == Decimal("1.0")
    gate.evaluate.assert_awaited_once()
    ctx_arg = gate.evaluate.call_args.args[0]
    assert ctx_arg.instrument_id == instrument_id
    assert ctx_arg.broker_id == "ibkr"  # capability_broker_id("ibkr-paper") → "ibkr"
    registry.get_client.assert_awaited_once_with("ibkr-paper")
