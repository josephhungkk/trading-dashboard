from decimal import Decimal

import pytest

from app.services.combos.pnl_envelope import compute_envelope
from app.services.combos.types import ComboSpec, LegSpec

pytestmark = pytest.mark.no_db


def _spec(strategy, legs):
    return ComboSpec(
        strategy_type=strategy,
        underlying_symbol="AAPL",
        underlying_canonical_id="AAPL",
        legs=legs,
        tif="DAY",
        account_id="a",
    )


def _leg(side, strike, expiry="2026-01-17", put_call="C"):
    return LegSpec(
        instrument_id=1,
        side=side,
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        expiry=expiry,
        strike=Decimal(str(strike)),
        put_call=put_call,
    )


def test_vertical_debit_envelope():
    legs = [_leg("buy", 250), _leg("sell", 260)]
    spec = _spec("VERTICAL", legs)
    mids = {0: Decimal("5.275"), 1: Decimal("2.175")}
    env = compute_envelope(spec, mids)
    assert env.kind == "DEBIT"
    assert env.net_debit_credit == Decimal("3.10000000")
    assert env.max_loss == Decimal("310.00000000")
    assert env.max_profit == Decimal("690.00000000")
    assert len(env.break_even) == 1
    assert env.break_even[0] == Decimal("253.10000000")


def test_vertical_credit_envelope():
    legs = [_leg("sell", 250), _leg("buy", 260)]
    spec = _spec("VERTICAL", legs)
    mids = {0: Decimal("5.275"), 1: Decimal("2.175")}
    env = compute_envelope(spec, mids)
    assert env.kind == "CREDIT"
    assert env.net_debit_credit == Decimal("3.10000000")
    assert env.max_profit == Decimal("310.00000000")
    assert env.max_loss == Decimal("690.00000000")


def test_straddle_debit_has_two_breakevens():
    legs = [_leg("buy", 250, put_call="C"), _leg("buy", 250, put_call="P")]
    spec = _spec("STRADDLE", legs)
    mids = {0: Decimal("5"), 1: Decimal("4")}
    env = compute_envelope(spec, mids)
    assert env.kind == "DEBIT"
    assert len(env.break_even) == 2
    assert env.max_loss == Decimal("900.00000000")
    assert env.max_profit is None


def test_short_straddle_unbounded():
    legs = [_leg("sell", 250, put_call="C"), _leg("sell", 250, put_call="P")]
    spec = _spec("STRADDLE", legs)
    mids = {0: Decimal("5"), 1: Decimal("4")}
    env = compute_envelope(spec, mids)
    assert env.kind == "CREDIT"
    assert env.max_loss is None
    assert len(env.break_even) == 0
