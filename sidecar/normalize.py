"""Decimal-safe IBKR value normalization."""

from __future__ import annotations

from decimal import Decimal
from math import isinf, isnan
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # The generated proto module may not exist before scripts/proto-gen.sh
    # has run; tolerate either state without a flapping mypy diagnostic.
    from sidecar._generated.broker.v1 import (
        broker_pb2,  # type: ignore[import-not-found, unused-ignore]
    )

# UK exchanges where IBKR returns GBP-denominated quotes in pence (GBX).
# CR-1: IBIS is Frankfurt/Xetra (EUR, NOT pence); excluding it from this set
# prevents silent /100 of EUR prices for dual-listed securities.
GBX_EXCHANGES: frozenset[str] = frozenset({"LSE", "LSEETF", "BATEUK", "CHIXUK"})
_PENCE_PER_POUND = Decimal("100")


def normalize_quote_currency(price: Decimal, currency: str, exchange: str) -> Decimal:
    """Normalize GBX-denominated UK quotes to pounds when IBKR reports currency as GBP."""
    if currency == "GBP" and exchange in GBX_EXCHANGES:
        return price / _PENCE_PER_POUND
    return price


def normalize_avg_cost(
    value: Decimal, account_number: str, config_unit: Literal["pounds", "pence"]
) -> Decimal:
    """Normalize average cost according to the per-account configured IBKR unit.

    `account_number` is accepted for caller-side context but is intentionally
    not logged here (callers log at the position level — avoid per-call noise).
    """
    del account_number  # caller-context only; intentionally unused
    if config_unit == "pence":
        return value / _PENCE_PER_POUND
    return value


def decimal_str(value: float | Decimal | None) -> str:
    """Convert a numeric value to a decimal string. Missing/NaN/Inf -> '0'."""
    if value is None:
        return "0"
    if isinstance(value, float):
        if isnan(value) or isinf(value):
            return "0"
        return str(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            return "0"
        return str(value)
    return str(Decimal(str(value)))


def to_money_proto(value: Decimal, currency: str) -> broker_pb2.Money:
    """Build a broker.v1 Money proto without making generated code a hard import."""
    try:
        from sidecar._generated.broker.v1 import broker_pb2
    except ImportError as exc:
        msg = "Generated broker proto modules are missing; run sidecar/scripts/proto-gen.sh first."
        raise ImportError(msg) from exc

    return broker_pb2.Money(value=str(value), currency=currency)
