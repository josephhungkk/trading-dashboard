"""Phase 9 T-8 — BarService skeleton tests."""

from __future__ import annotations

import pytest

from app.services.bar_service import _SOURCE_PRIORITY, BarService

pytestmark = [pytest.mark.unit]


def test_source_priority_mapping_is_canonical() -> None:
    assert _SOURCE_PRIORITY == {
        "schwab": 1,
        "alpaca": 2,
        "ibkr": 3,
        "futu": 4,
        "aggregator-schwab": 99,
        "aggregator-alpaca": 99,
        "aggregator-ibkr": 99,
        "aggregator-futu": 99,
    }


@pytest.mark.asyncio
async def test_bar_service_lifespan_start_stop_idempotent() -> None:
    svc = BarService()
    await svc.start()
    await svc.start()  # idempotent — no error
    await svc.stop()
    await svc.stop()  # idempotent — no error
