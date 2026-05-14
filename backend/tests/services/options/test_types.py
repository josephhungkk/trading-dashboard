"""Tests for Phase 12 options Pydantic types."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.options.types import (
    GreeksSnapshot,
    NonOptionDetails,
    OptionDetails,
    parse_instrument_meta,
)

pytestmark = pytest.mark.no_db


def test_parse_empty_meta_returns_non_option() -> None:
    parsed = parse_instrument_meta({})

    assert isinstance(parsed, NonOptionDetails)
    assert parsed.asset_class == ""


def test_parse_stock_meta_returns_non_option() -> None:
    parsed = parse_instrument_meta({"asset_class": "STOCK"})

    assert isinstance(parsed, NonOptionDetails)
    assert parsed.asset_class == "STOCK"


def test_parse_option_meta_returns_option_details() -> None:
    parsed = parse_instrument_meta(
        {
            "asset_class": "OPTION",
            "underlying_canonical_id": "stock:SPY:US",
            "option_type": "CALL",
            "strike": "450.00",
            "expiry": "2026-06-20",
        }
    )

    assert isinstance(parsed, OptionDetails)
    assert parsed.multiplier == 100
    assert parsed.style == "AMERICAN"
    assert parsed.strike == Decimal("450.00")


def test_parse_option_meta_missing_required_fields_raises() -> None:
    with pytest.raises(ValidationError):
        parse_instrument_meta({"asset_class": "OPTION"})


def test_option_details_multiplier_defaults_to_100() -> None:
    details = OptionDetails(
        underlying_canonical_id="stock:SPY:US",
        option_type="PUT",
        strike=Decimal("400.00"),
        expiry="2026-06-20",
    )

    assert details.multiplier == 100


def test_greeks_snapshot_accepts_all_none_greeks() -> None:
    snapshot = GreeksSnapshot(instrument_id=123)

    assert snapshot.delta is None
    assert snapshot.gamma is None
    assert snapshot.theta is None
    assert snapshot.vega is None
    assert snapshot.rho is None
    assert snapshot.iv is None
    assert snapshot.iv_rank is None
