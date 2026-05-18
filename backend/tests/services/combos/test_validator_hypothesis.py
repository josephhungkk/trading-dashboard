from decimal import Decimal

import pytest

from app.services.combos.strategy_validator import ComboValidationError, validate
from app.services.combos.types import LegSpec

pytestmark = pytest.mark.no_db
hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")
given = hypothesis.given
settings = hypothesis.settings

STRIKES = [
    Decimal(str(s))
    for s in [
        50,
        52.5,
        55,
        57.5,
        60,
        65,
        70,
        75,
        80,
        90,
        100,
        110,
        120,
        130,
        150,
        175,
        200,
        225,
        250,
        275,
        300,
        350,
        400,
        450,
        500,
    ]
]
EXPIRIES = ["2026-01-17", "2026-04-17"]
SIDES = ["buy", "sell"]
PUT_CALLS = ["C", "P"]
STRATEGIES = ["VERTICAL", "CALENDAR", "DIAGONAL", "STRADDLE", "STRANGLE"]


def _make_leg(side, strike, expiry, put_call):
    return LegSpec(
        instrument_id=1,
        side=side,
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        expiry=expiry,
        strike=strike,
        put_call=put_call,
    )


@settings(max_examples=200)
@given(
    strategy=st.sampled_from(STRATEGIES),
    s1=st.sampled_from(STRIKES),
    s2=st.sampled_from(STRIKES),
    e1=st.sampled_from(EXPIRIES),
    e2=st.sampled_from(EXPIRIES),
    side1=st.sampled_from(SIDES),
    side2=st.sampled_from(SIDES),
    pc1=st.sampled_from(PUT_CALLS),
    pc2=st.sampled_from(PUT_CALLS),
)
def test_validator_always_returns_spec_or_known_reason(
    strategy, s1, s2, e1, e2, side1, side2, pc1, pc2
):
    legs = [_make_leg(side1, s1, e1, pc1), _make_leg(side2, s2, e2, pc2)]
    known_reasons = {
        "expiry_mismatch",
        "different_expiry_required",
        "same_strike_required",
        "different_strike_required",
        "expiry_or_strike_must_differ",
        "same_put_call_required",
        "opposite_put_call_required",
        "opposite_side_required",
        "same_side_required",
        "currency_mismatch",
    }
    try:
        spec = validate(strategy, legs, "AAPL", "AAPL", "DAY", "acct")
        assert spec.strategy_type == strategy
    except ComboValidationError as e:
        assert e.reason in known_reasons
