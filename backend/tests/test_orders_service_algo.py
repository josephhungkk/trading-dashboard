"""Tests for algo order handling in orders_service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_validate_pre_dispatch_algo_requires_limit_rejects_market():
    """ICEBERG with MARKET order type should 422 algo_requires_limit."""
    from app.services.orders_service import PreviewUnavailable, validate_pre_dispatch

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    cfg.get_bool = AsyncMock(return_value=False)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="MARKET",
            tif="DAY",
            algo_strategy="ICEBERG",
        )
    assert exc_info.value.status_code == 422
    assert "algo_requires_limit" in str(exc_info.value.payload)


@pytest.mark.asyncio
async def test_validate_pre_dispatch_dark_ice_requires_limit():
    from app.services.orders_service import PreviewUnavailable, validate_pre_dispatch

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    cfg.get_bool = AsyncMock(return_value=False)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="MARKET",
            tif="DAY",
            algo_strategy="DARK_ICE",
        )
    assert "algo_requires_limit" in str(exc_info.value.payload)


@pytest.mark.asyncio
async def test_validate_pre_dispatch_adaptive_no_limit_required():
    """ADAPTIVE does not require LIMIT order type."""
    from app.services.orders_service import validate_pre_dispatch

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    # Should NOT raise — ADAPTIVE works with any order type
    await validate_pre_dispatch(
        cfg=cfg,
        capability=capability,
        broker_label="isa-paper",
        asset_class="STOCK",
        order_type="MARKET",
        tif="DAY",
        algo_strategy="ADAPTIVE",
        skip_operational_checks=True,
    )


@pytest.mark.asyncio
async def test_validate_pre_dispatch_bracket_sl_with_algo_rejects():
    """algo_strategy on bracket SL/TP legs must 422."""
    from app.services.orders_service import PreviewUnavailable, validate_pre_dispatch

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=None)
    capability = MagicMock()
    capability.is_supported = AsyncMock(return_value=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label="isa-paper",
            asset_class="STOCK",
            order_type="LIMIT",
            tif="GTC",
            algo_strategy="TWAP",
            is_bracket_leg=True,
            skip_operational_checks=True,
        )
    assert "algo_on_bracket_leg_unsupported" in str(exc_info.value.payload)
