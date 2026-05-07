from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import REGISTRY

from app.services.order_capability_service import OrderCapabilityService


def _result(row: dict[str, object] | None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


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
        assert await svc.is_supported(broker_id, "CRYPTO", f"LIMIT_{idx}", "DAY") is True

    after = (
        REGISTRY.get_sample_value(
            "order_capability_cache_evictions_total", {"broker_id": broker_id}
        )
        or 0.0
    )
    assert after > before
