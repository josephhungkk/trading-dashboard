"""Tests for sidecar.normalize (Phase 4 Task 9)."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from sidecar_ibkr.normalize import (
    GBX_EXCHANGES,
    decimal_str,
    normalize_avg_cost,
    normalize_quote_currency,
)


# CR-1: IBIS is Frankfurt/Xetra (EUR) and MUST NOT divide quotes by 100.
# Anchor the membership table here so a future refactor that re-adds IBIS
# trips the unit suite immediately.
def test_gbx_exchanges_includes_uk_venues() -> None:
    assert "LSE" in GBX_EXCHANGES
    assert "LSEETF" in GBX_EXCHANGES
    assert "BATEUK" in GBX_EXCHANGES
    assert "CHIXUK" in GBX_EXCHANGES


def test_gbx_exchanges_excludes_non_uk_venues() -> None:
    """CR-1: critical regression guard."""
    assert "IBIS" not in GBX_EXCHANGES, "Frankfurt/Xetra is EUR, not pence"
    assert "NASDAQ" not in GBX_EXCHANGES
    assert "NYSE" not in GBX_EXCHANGES
    assert "SMART" not in GBX_EXCHANGES


@pytest.mark.parametrize(
    ("price", "currency", "exchange", "expected"),
    [
        # GBX UK quote -> GBP
        (Decimal("12000"),    "GBP", "LSE",     Decimal("120")),
        (Decimal("12000"),    "GBP", "LSEETF",  Decimal("120")),
        (Decimal("12000"),    "GBP", "BATEUK",  Decimal("120")),
        (Decimal("12000"),    "GBP", "CHIXUK",  Decimal("120")),
        # Decimal precision preserved through division
        (Decimal("12000.45"), "GBP", "LSE",     Decimal("120.0045")),
        # Non-GBX exchanges left alone even when currency is GBP
        (Decimal("12000"),    "GBP", "NASDAQ",  Decimal("12000")),
        # CR-1: IBIS (Frankfurt) MUST NOT scale even with currency=GBP
        (Decimal("12000"),    "GBP", "IBIS",    Decimal("12000")),
        # USD never scales
        (Decimal("100"),      "USD", "NASDAQ",  Decimal("100")),
        (Decimal("100"),      "USD", "LSE",     Decimal("100")),
    ],
)
def test_normalize_quote_currency(
    price: Decimal, currency: str, exchange: str, expected: Decimal
) -> None:
    assert normalize_quote_currency(price, currency, exchange) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("128.44"),  "pounds", Decimal("128.44")),
        (Decimal("12844"),   "pence",  Decimal("128.44")),
        (Decimal("0"),       "pounds", Decimal("0")),
        (Decimal("0"),       "pence",  Decimal("0")),
    ],
)
def test_normalize_avg_cost(
    value: Decimal, unit: str, expected: Decimal
) -> None:
    assert normalize_avg_cost(value, "U1234567", unit) == expected  # type: ignore[arg-type]


def test_decimal_str_handles_none() -> None:
    assert decimal_str(None) == "0"


def test_decimal_str_handles_finite_float() -> None:
    assert decimal_str(1.5) == "1.5"


def test_decimal_str_handles_finite_decimal() -> None:
    assert decimal_str(Decimal("128.44")) == "128.44"


def test_decimal_str_handles_nan_float() -> None:
    """HIGH-2: NaN float must not surface as 'NaN' string in proto."""
    assert decimal_str(float("nan")) == "0"


def test_decimal_str_handles_inf_float() -> None:
    """HIGH-2: positive infinity must collapse to '0' (proto/JSON safe)."""
    assert decimal_str(math.inf) == "0"


def test_decimal_str_handles_negative_inf_float() -> None:
    """HIGH-2: negative infinity must also collapse to '0'."""
    assert decimal_str(-math.inf) == "0"


def test_decimal_str_handles_nan_decimal() -> None:
    assert decimal_str(Decimal("NaN")) == "0"


def test_decimal_str_handles_inf_decimal() -> None:
    """HIGH-2: Decimal('Infinity') must not leak through to proto."""
    assert decimal_str(Decimal("Infinity")) == "0"
    assert decimal_str(Decimal("-Infinity")) == "0"
