"""FutureDetails discriminated union tests."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.services.options.types import FutureDetails, parse_instrument_meta


def _future_meta(settlement_type: str = "CASH", first_notice: date | None = None) -> dict:
    return {
        "asset_class": "FUTURE",
        "contract_month": "202506",
        "tick_size": "0.25",
        "tick_value": "12.50",
        "multiplier": "50",
        "first_notice_day": first_notice.isoformat() if first_notice else None,
        "expiry": "2025-06-20",
        "settlement_type": settlement_type,
        "exchange": "CME",
        "underlying_symbol": "ES",
    }


def test_future_details_round_trip_cash() -> None:
    raw = _future_meta("CASH")
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert result.multiplier == Decimal("50")
    assert result.tick_size == Decimal("0.25")
    assert result.settlement_type == "CASH"
    assert result.first_notice_day is None
    assert result.underlying_symbol == "ES"


def test_future_details_round_trip_physical() -> None:
    raw = _future_meta("PHYSICAL", first_notice=date(2025, 5, 28))
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert result.settlement_type == "PHYSICAL"
    assert result.first_notice_day == date(2025, 5, 28)


def test_future_details_multiplier_is_decimal() -> None:
    raw = _future_meta()
    result = parse_instrument_meta(json.dumps(raw))
    assert isinstance(result, FutureDetails)
    assert isinstance(result.multiplier, Decimal)


def test_non_option_still_works_after_union_expansion() -> None:
    result = parse_instrument_meta("{}")
    from app.services.options.types import NonOptionDetails

    assert isinstance(result, NonOptionDetails)


def test_option_still_works_after_union_expansion() -> None:
    raw = json.dumps(
        {
            "asset_class": "OPTION",
            "strike": "420.00",
            "put_call": "CALL",
            "expiry_iso": "2025-06-20",
            "multiplier": "100",
            "exchange": "CBOE",
        }
    )
    result = parse_instrument_meta(raw)
    from app.services.options.types import OptionDetails

    assert isinstance(result, OptionDetails)
    assert result.multiplier == Decimal("100")
