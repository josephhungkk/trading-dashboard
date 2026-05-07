from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from app.services.order_capability_service import OrderCapabilityService


def _result(row: dict[str, object] | None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


@pytest.mark.asyncio
async def test_is_supported_4tuple_signature_works() -> None:
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    svc = OrderCapabilityService(db=db, redis=MagicMock())

    assert await svc.is_supported("alpaca", "CRYPTO", "MARKET", "DAY") is True

    _, params = db.execute.await_args.args
    assert params == {
        "broker_id": "alpaca",
        "asset_class": "CRYPTO",
        "order_type": "MARKET",
        "time_in_force": "DAY",
    }


@pytest.mark.asyncio
async def test_etf_bucket_collapses_to_stock() -> None:
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    svc = OrderCapabilityService(db=db, redis=MagicMock())

    assert await svc.is_supported("alpaca", "STOCK", "MARKET", "DAY") is True
    assert await svc.is_supported("alpaca", "ETF", "MARKET", "DAY") is True

    assert db.execute.await_count == 1
    _, params = db.execute.await_args.args
    assert params["asset_class"] == "STOCK"


@pytest.mark.asyncio
async def test_3tuple_deprecation_shim_emits_warning() -> None:
    broker_id = "alpaca"
    before = (
        REGISTRY.get_sample_value(
            "order_capability_legacy_3tuple_calls_total", {"broker_id": broker_id}
        )
        or 0.0
    )
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    svc = OrderCapabilityService(db=db, redis=MagicMock())

    with patch("app.services.order_capability_service.structlog.get_logger") as get_logger:
        logger = MagicMock()
        get_logger.return_value = logger
        assert await svc.is_supported_3tuple_deprecated(broker_id, "MARKET", "DAY") is True

    logger.warning.assert_called_once_with(
        "order_capability.legacy_3tuple_call",
        broker_id=broker_id,
    )
    after = (
        REGISTRY.get_sample_value(
            "order_capability_legacy_3tuple_calls_total", {"broker_id": broker_id}
        )
        or 0.0
    )
    assert after == before + 1


@pytest.mark.asyncio
async def test_cache_eviction_increments_counter() -> None:
    broker_id = "alpaca"
    before = (
        REGISTRY.get_sample_value(
            "order_capability_cache_evictions_total", {"broker_id": broker_id}
        )
        or 0.0
    )
    db = AsyncMock()
    db.execute.return_value = _result({"is_supported": True, "notes": ""})
    svc = OrderCapabilityService(db=db, redis=MagicMock())

    for idx in range(2049):
        assert await svc.is_supported(broker_id, "CRYPTO", f"MARKET_{idx}", "DAY") is True

    after = (
        REGISTRY.get_sample_value(
            "order_capability_cache_evictions_total", {"broker_id": broker_id}
        )
        or 0.0
    )
    assert after > before
