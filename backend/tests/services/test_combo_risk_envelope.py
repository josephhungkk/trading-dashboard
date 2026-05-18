from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.combos.types import ComboContext, ComboEnvelope, LegContext
from app.services.risk_service import RiskService

pytestmark = pytest.mark.no_db


def _ctx(max_loss, kind="DEBIT", unbounded=False):
    env = ComboEnvelope(
        net_debit_credit=Decimal("3.1"),
        kind=kind,
        max_loss=None if unbounded else Decimal(str(max_loss)),
        max_profit=Decimal("690") if not unbounded else None,
        break_even=[Decimal("253.1")],
    )
    return ComboContext(
        account_id="acct-1",
        mode="preview",
        legs=[
            LegContext(
                leg_idx=0,
                instrument_id=1,
                side="buy",
                qty=Decimal("1"),
                position_effect="OPEN",
            )
        ],
        envelope=env,
    )


def _svc(max_combo_loss_native=None, naked_margin_enabled=True):
    limits = MagicMock()
    limits.max_combo_loss_native = max_combo_loss_native
    limits.naked_margin_enabled = naked_margin_enabled
    svc = RiskService.__new__(RiskService)
    svc._limits = limits
    return svc


@pytest.mark.asyncio
async def test_combo_block_when_max_loss_exceeds_limit():
    svc = _svc(max_combo_loss_native=Decimal("200"))
    ctx = _ctx(max_loss=310)
    result = await svc.evaluate_combo(ctx, mode="preview")
    assert any(b.check == "combo_max_loss" for b in result.blockers)


@pytest.mark.asyncio
async def test_combo_allow_within_limit():
    svc = _svc(max_combo_loss_native=Decimal("500"))
    ctx = _ctx(max_loss=3.1)
    result = await svc.evaluate_combo(ctx, mode="preview")
    assert not any(b.check == "combo_max_loss" for b in result.blockers)


@pytest.mark.asyncio
async def test_unbounded_combo_blocked_without_naked_margin():
    svc = _svc(naked_margin_enabled=False)
    ctx = _ctx(max_loss=0, unbounded=True)
    result = await svc.evaluate_combo(ctx, mode="preview")
    assert any(b.check == "combo_unbounded" for b in result.blockers)


@pytest.mark.asyncio
async def test_bounded_credit_vertical_not_blocked_by_naked():
    svc = _svc(naked_margin_enabled=False, max_combo_loss_native=Decimal("1000"))
    ctx = _ctx(max_loss=310, kind="CREDIT")
    result = await svc.evaluate_combo(ctx, mode="preview")
    naked_blocks = [b for b in result.blockers if "naked" in b.check]
    assert not naked_blocks


@pytest.mark.asyncio
async def test_evaluate_combo_returns_gate_verdict():
    from app.schemas.risk import GateVerdict

    svc = _svc()
    ctx = _ctx(max_loss=310)
    result = await svc.evaluate_combo(ctx, mode="preview")
    assert isinstance(result, GateVerdict)
    assert result.final_verdict in ("allow", "warn", "block")
