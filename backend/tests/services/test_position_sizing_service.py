from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.position_sizing_math import (
    compute_fixed_fractional,
    compute_risk_per_trade,
    compute_vol_targeted,
)


def test_fixed_fractional_2pct_of_100k_at_50_is_40_shares() -> None:
    """Spec §4.4 golden vector: 2% of $100k NLV at $50 = 40 shares."""
    qty, notional = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        risk_pct=Decimal("2"),
    )
    assert qty == Decimal("40")
    assert notional == Decimal("2000")


def test_risk_per_trade_1pct_at_1_dollar_stop_is_1000_shares() -> None:
    """Spec §4.4 golden vector: 1% of $100k NLV at $1 stop distance = 1000 shares."""
    qty, notional, risk_per_share = compute_risk_per_trade(
        nlv_base=Decimal("100000"),
        entry_base=Decimal("50"),
        stop_base=Decimal("49"),
        side="buy",
        risk_pct=Decimal("1"),
    )
    assert qty == Decimal("1000")
    assert risk_per_share == Decimal("1")
    assert notional == Decimal("50000")


def test_risk_per_trade_rejects_buy_with_stop_above_entry() -> None:
    with pytest.raises(ValueError, match=r"stop.*below.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("51"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_sell_with_stop_below_entry() -> None:
    with pytest.raises(ValueError, match=r"stop.*above.*entry"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("49"),
            side="sell",
            risk_pct=Decimal("1"),
        )


def test_risk_per_trade_rejects_zero_distance() -> None:
    with pytest.raises(ValueError, match=r"zero.distance|entry == stop"):
        compute_risk_per_trade(
            nlv_base=Decimal("100000"),
            entry_base=Decimal("50"),
            stop_base=Decimal("50"),
            side="buy",
            risk_pct=Decimal("1"),
        )


def test_vol_targeted_15pct_at_25pct_vol_at_50_is_1200_shares() -> None:
    """Spec §4.4 golden: 15% target vol with 25% asset vol at $50 → 1200 shares.

    qty = (0.15 * 100000) / (0.25 * 50) = 15000 / 12.5 = 1200.
    """
    qty, notional = compute_vol_targeted(
        nlv_base=Decimal("100000"),
        price_base=Decimal("50"),
        target_vol_pct=Decimal("15"),
        asset_vol_annualized=Decimal("0.25"),
    )
    assert qty == Decimal("1200")
    assert notional == Decimal("60000")


def test_vol_targeted_rejects_zero_vol() -> None:
    with pytest.raises(ValueError, match="zero_volatility"):
        compute_vol_targeted(
            nlv_base=Decimal("100000"),
            price_base=Decimal("50"),
            target_vol_pct=Decimal("15"),
            asset_vol_annualized=Decimal("0"),
        )


def test_fixed_fractional_floors_not_rounds() -> None:
    """3% of $100k at $33 = 3000/33 = 90.909... → floor to 90."""
    qty, _ = compute_fixed_fractional(
        nlv_base=Decimal("100000"),
        price_base=Decimal("33"),
        risk_pct=Decimal("3"),
    )
    assert qty == Decimal("90")
