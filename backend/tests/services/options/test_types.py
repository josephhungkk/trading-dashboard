"""Tests for InstrumentMeta Pydantic discriminated union."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError


def test_parse_stock_empty_meta() -> None:
    from app.services.options.types import NonOptionDetails, parse_instrument_meta

    result = parse_instrument_meta({})
    assert isinstance(result, NonOptionDetails)
    assert result.asset_class == ""


def test_parse_stock_explicit() -> None:
    from app.services.options.types import NonOptionDetails, parse_instrument_meta

    result = parse_instrument_meta({"asset_class": "STOCK"})
    assert isinstance(result, NonOptionDetails)


def test_parse_option_details() -> None:
    from app.services.options.types import OptionDetails, parse_instrument_meta

    raw = {
        "asset_class": "OPTION",
        "underlying_canonical_id": "stock:SPY:US",
        "strike": "450.00",
        "expiry": "2025-01-17",
        "put_call": "C",
        "multiplier": 100,
        "style": "A",
    }
    result = parse_instrument_meta(raw)
    assert isinstance(result, OptionDetails)
    assert result.strike == Decimal("450.00")
    assert result.multiplier == 100
    assert result.style == "A"


def test_option_details_requires_multiplier() -> None:
    from app.services.options.types import OptionDetails

    with pytest.raises(ValidationError):
        OptionDetails(
            underlying_canonical_id="stock:SPY:US",
            strike=Decimal("450"),
            expiry=date(2025, 1, 17),
            put_call="C",
            style="A",
            # multiplier missing
        )


def test_option_details_requires_style() -> None:
    from app.services.options.types import OptionDetails

    with pytest.raises(ValidationError):
        OptionDetails(
            underlying_canonical_id="stock:SPY:US",
            strike=Decimal("450"),
            expiry=date(2025, 1, 17),
            put_call="C",
            multiplier=100,
            # style missing
        )


def test_unknown_asset_class_raises() -> None:
    from app.services.options.types import parse_instrument_meta

    with pytest.raises(ValidationError):
        parse_instrument_meta({"asset_class": "BOND"})


def test_greeks_snapshot_clamping() -> None:
    from app.services.options.types import GreeksSnapshot

    snap = GreeksSnapshot(
        delta=Decimal("99999"),
        gamma=Decimal("0.028"),
        theta=Decimal("-0.12"),
        vega=Decimal("0.31"),
        rho=Decimal("0.05"),
        iv=Decimal("0.175"),
    )
    assert snap.delta == Decimal("9999.999999")


def test_subscription_handle_fields() -> None:
    from app.services.options.types import SubscriptionHandle

    h = SubscriptionHandle(conid="12345", canonical_id=None, channel="greeks.options.12345")
    assert h.conid == "12345"
    assert h.canonical_id is None
