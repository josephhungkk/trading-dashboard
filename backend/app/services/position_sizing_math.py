"""Phase 10b.1 pure-math sizing functions. Decimal end-to-end; never float.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §2.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Literal


def _floor(quotient: Decimal) -> Decimal:
    """Decimal-correct floor — never round-toward-zero (int() truncation)."""
    return quotient.to_integral_value(rounding=ROUND_FLOOR)


def compute_fixed_fractional(
    *, nlv_base: Decimal, price_base: Decimal, risk_pct: Decimal
) -> tuple[Decimal, Decimal]:
    """qty = floor((NLV * risk_pct / 100) / price_base). Returns (qty, notional)."""
    if price_base <= 0:
        raise ValueError("price_base must be > 0")
    notional_target = (nlv_base * risk_pct / Decimal(100)).quantize(Decimal("1e-8"))
    qty = _floor(notional_target / price_base)
    notional = (qty * price_base).quantize(Decimal("1e-8"))
    return qty, notional


def compute_risk_per_trade(
    *,
    nlv_base: Decimal,
    entry_base: Decimal,
    stop_base: Decimal,
    side: Literal["buy", "sell"],
    risk_pct: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """qty = floor((NLV * risk_pct / 100) / |entry - stop|).

    Returns (qty, notional_at_entry, risk_per_share).
    Side-aware validation: BUY needs stop < entry; SELL needs stop > entry.
    """
    if entry_base == stop_base:
        raise ValueError("zero_distance: entry == stop")
    if side == "buy" and stop_base >= entry_base:
        raise ValueError("for BUY, stop must be below entry")
    if side == "sell" and stop_base <= entry_base:
        raise ValueError("for SELL, stop must be above entry")

    risk_per_share = abs(entry_base - stop_base)
    risk_budget = (nlv_base * risk_pct / Decimal(100)).quantize(Decimal("1e-8"))
    qty = _floor(risk_budget / risk_per_share)
    notional = (qty * entry_base).quantize(Decimal("1e-8"))
    return qty, notional, risk_per_share


def compute_vol_targeted(
    *,
    nlv_base: Decimal,
    price_base: Decimal,
    target_vol_pct: Decimal,
    asset_vol_annualized: Decimal,
) -> tuple[Decimal, Decimal]:
    """qty = floor((target_vol_pct/100 * NLV) / (asset_vol * price_base)).

    Returns (qty, notional). asset_vol_annualized is a unitless fraction
    (e.g., 0.25 for 25%). Caller is responsible for sourcing the vol
    (either via VolatilityService or user override).
    """
    if asset_vol_annualized <= 0:
        raise ValueError("zero_volatility: asset_vol_annualized must be > 0")
    if price_base <= 0:
        raise ValueError("price_base must be > 0")
    notional_budget = (target_vol_pct / Decimal(100) * nlv_base).quantize(Decimal("1e-8"))
    qty = _floor(notional_budget / (asset_vol_annualized * price_base))
    notional = (qty * price_base).quantize(Decimal("1e-8"))
    return qty, notional
