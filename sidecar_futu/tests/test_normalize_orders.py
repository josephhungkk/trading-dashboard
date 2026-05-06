"""T-F.3 — to_futu_order_params: TRAIL + GTD + auction-session type normalization."""
from __future__ import annotations

import pytest
import futu as ft

from sidecar_futu.normalize import to_futu_order_params


class TestTrailOrders:
    def test_trail_percent_maps_ratio(self) -> None:
        """TRAIL + PERCENT trail_offset_type → ft.TrailType.RATIO."""
        params = to_futu_order_params(
            "TRAIL", "DAY", "SEHK", trail_offset=2.5, trail_offset_type="PERCENT"
        )
        assert params["order_type"] == ft.OrderType.TRAILING_STOP
        assert params["trail_type"] == ft.TrailType.RATIO
        assert params["aux_price"] == pytest.approx(2.5)

    def test_trail_amount_maps_amount(self) -> None:
        """TRAIL + AMOUNT trail_offset_type → ft.TrailType.AMOUNT."""
        params = to_futu_order_params(
            "TRAIL", "DAY", "SEHK", trail_offset=1.0, trail_offset_type="AMOUNT"
        )
        assert params["order_type"] == ft.OrderType.TRAILING_STOP
        assert params["trail_type"] == ft.TrailType.AMOUNT
        assert params["aux_price"] == pytest.approx(1.0)

    def test_trail_limit_percent_maps_trailing_stop_limit(self) -> None:
        """TRAIL_LIMIT + PERCENT → ft.OrderType.TRAILING_STOP_LIMIT + RATIO."""
        params = to_futu_order_params(
            "TRAIL_LIMIT", "GTC", "SEHK", trail_offset=3.0, trail_offset_type="PERCENT"
        )
        assert params["order_type"] == ft.OrderType.TRAILING_STOP_LIMIT
        assert params["trail_type"] == ft.TrailType.RATIO

    def test_trail_limit_amount_maps_amount(self) -> None:
        """TRAIL_LIMIT + AMOUNT → ft.OrderType.TRAILING_STOP_LIMIT + AMOUNT."""
        params = to_futu_order_params(
            "TRAIL_LIMIT", "GTC", "SEHK", trail_offset=0.5, trail_offset_type="AMOUNT"
        )
        assert params["order_type"] == ft.OrderType.TRAILING_STOP_LIMIT
        assert params["trail_type"] == ft.TrailType.AMOUNT


class TestHKEXAuctionRejection:
    def test_hk_moo_rejected(self) -> None:
        """MOO + HKEX → ValueError('unsupported_for_hkex')."""
        with pytest.raises(ValueError, match="unsupported_for_hkex"):
            to_futu_order_params("MOO", "DAY", "HKEX")

    def test_hk_loo_rejected(self) -> None:
        """LOO + HKEX → ValueError('unsupported_for_hkex')."""
        with pytest.raises(ValueError, match="unsupported_for_hkex"):
            to_futu_order_params("LOO", "DAY", "HKEX")

    def test_hk_loc_rejected(self) -> None:
        """LOC + HKEX → ValueError('unsupported_for_hkex')."""
        with pytest.raises(ValueError, match="unsupported_for_hkex"):
            to_futu_order_params("LOC", "DAY", "HKEX")

    def test_hk_moc_rejected(self) -> None:
        """MOC + HKEX → ValueError('unsupported_for_hkex')."""
        with pytest.raises(ValueError, match="unsupported_for_hkex"):
            to_futu_order_params("MOC", "DAY", "HKEX")

    def test_non_hkex_moo_allowed(self) -> None:
        """MOO on non-HKEX exchange should NOT raise."""
        # Should not raise; exchange is NYSE so restriction doesn't apply.
        params = to_futu_order_params("MOO", "DAY", "NYSE")
        assert "order_type" in params


class TestTimeInForce:
    def test_day_maps_to_day(self) -> None:
        params = to_futu_order_params("LIMIT", "DAY", "SEHK")
        assert params["time_in_force"] == ft.TimeInForce.DAY

    def test_gtc_maps_to_gtc(self) -> None:
        params = to_futu_order_params("LIMIT", "GTC", "SEHK")
        assert params["time_in_force"] == ft.TimeInForce.GTC

    def test_gtd_raises_not_implemented(self) -> None:
        """GTD is not available in this SDK version → NotImplementedError."""
        with pytest.raises(NotImplementedError, match="futu_gtd_unsupported"):
            to_futu_order_params("LIMIT", "GTD", "SEHK")

    def test_ioc_raises_not_implemented(self) -> None:
        """IOC is not available → NotImplementedError."""
        with pytest.raises(NotImplementedError):
            to_futu_order_params("LIMIT", "IOC", "SEHK")

    def test_fok_raises_not_implemented(self) -> None:
        """FOK is not available → NotImplementedError."""
        with pytest.raises(NotImplementedError):
            to_futu_order_params("LIMIT", "FOK", "SEHK")


class TestStandardOrderTypes:
    def test_limit_maps_normal(self) -> None:
        params = to_futu_order_params("LIMIT", "DAY", "SEHK")
        assert params["order_type"] == ft.OrderType.NORMAL

    def test_market_maps_market(self) -> None:
        params = to_futu_order_params("MARKET", "DAY", "SEHK")
        assert params["order_type"] == ft.OrderType.MARKET

    def test_stop_maps_stop(self) -> None:
        params = to_futu_order_params("STOP", "GTC", "SEHK")
        assert params["order_type"] == ft.OrderType.STOP

    def test_stop_limit_maps_stop_limit(self) -> None:
        params = to_futu_order_params("STOP_LIMIT", "GTC", "SEHK")
        assert params["order_type"] == ft.OrderType.STOP_LIMIT
