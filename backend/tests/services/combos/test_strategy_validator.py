from decimal import Decimal

import pytest

from app.services.combos.strategy_validator import ComboValidationError, validate
from app.services.combos.types import LegSpec

pytestmark = pytest.mark.no_db


def _leg(side, strike, expiry="2026-01-17", put_call="C", symbol="AAPL"):
    return LegSpec(
        instrument_id=1,
        side=side,
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol=symbol,
        exchange="SMART",
        currency="USD",
        expiry=expiry,
        strike=Decimal(str(strike)),
        put_call=put_call,
    )


def test_vertical_valid():
    legs = [_leg("buy", 250), _leg("sell", 260)]
    spec = validate("VERTICAL", legs, "AAPL", "AAPL", "DAY", "acct-1")
    assert spec.strategy_type == "VERTICAL"


def test_vertical_same_strike_rejected():
    with pytest.raises(ComboValidationError, match="different_strike_required"):
        validate(
            "VERTICAL",
            [_leg("buy", 250), _leg("sell", 250)],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_vertical_different_expiry_rejected():
    with pytest.raises(ComboValidationError, match="expiry_mismatch"):
        validate(
            "VERTICAL",
            [
                _leg("buy", 250, expiry="2026-01-17"),
                _leg("sell", 260, expiry="2026-04-17"),
            ],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_vertical_same_side_rejected():
    with pytest.raises(ComboValidationError, match="opposite_side_required"):
        validate(
            "VERTICAL",
            [_leg("buy", 250), _leg("buy", 260)],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_vertical_different_put_call_rejected():
    with pytest.raises(ComboValidationError, match="same_put_call_required"):
        validate(
            "VERTICAL",
            [_leg("buy", 250, put_call="C"), _leg("sell", 260, put_call="P")],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_calendar_valid():
    legs = [
        _leg("buy", 250, expiry="2026-04-17"),
        _leg("sell", 250, expiry="2026-01-17"),
    ]
    spec = validate("CALENDAR", legs, "AAPL", "AAPL", "DAY", "a")
    assert spec.strategy_type == "CALENDAR"


def test_calendar_different_strike_rejected():
    with pytest.raises(ComboValidationError, match="same_strike_required"):
        validate(
            "CALENDAR",
            [
                _leg("buy", 250, expiry="2026-04-17"),
                _leg("sell", 260, expiry="2026-01-17"),
            ],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_straddle_valid():
    legs = [_leg("buy", 250, put_call="C"), _leg("buy", 250, put_call="P")]
    validate("STRADDLE", legs, "AAPL", "AAPL", "DAY", "a")


def test_straddle_different_put_call_rejected():
    with pytest.raises(ComboValidationError, match="opposite_put_call_required"):
        validate(
            "STRADDLE",
            [_leg("buy", 250, put_call="C"), _leg("buy", 250, put_call="C")],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_strangle_valid():
    legs = [_leg("buy", 240, put_call="P"), _leg("buy", 260, put_call="C")]
    validate("STRANGLE", legs, "AAPL", "AAPL", "DAY", "a")


def test_diagonal_valid():
    legs = [
        _leg("buy", 250, expiry="2026-04-17"),
        _leg("sell", 260, expiry="2026-01-17"),
    ]
    validate("DIAGONAL", legs, "AAPL", "AAPL", "DAY", "a")


def test_diagonal_same_strike_same_expiry_rejected():
    with pytest.raises(ComboValidationError):
        validate(
            "DIAGONAL",
            [
                _leg("buy", 250, expiry="2026-01-17"),
                _leg("sell", 250, expiry="2026-01-17"),
            ],
            "AAPL",
            "AAPL",
            "DAY",
            "a",
        )


def test_currency_mismatch_rejected():
    leg1 = LegSpec(
        instrument_id=1,
        side="buy",
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol="AAPL",
        exchange="SMART",
        currency="USD",
        expiry="2026-01-17",
        strike=Decimal("250"),
        put_call="C",
    )
    leg2 = LegSpec(
        instrument_id=2,
        side="sell",
        qty=Decimal("1"),
        position_effect="OPEN",
        symbol="AAPL",
        exchange="SMART",
        currency="HKD",
        expiry="2026-01-17",
        strike=Decimal("260"),
        put_call="C",
    )
    with pytest.raises(ComboValidationError, match="currency_mismatch"):
        validate("VERTICAL", [leg1, leg2], "AAPL", "AAPL", "DAY", "a")
