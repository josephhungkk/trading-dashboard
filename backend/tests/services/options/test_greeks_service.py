"""Tests for OptionGreeksService — upsert guard, eviction, clamping."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_service(*, db=None, redis=None):
    from app.services.options.greeks_service import OptionGreeksService

    db = db or AsyncMock()
    redis = redis or AsyncMock()
    return OptionGreeksService(db=db, redis=redis)


def _make_snapshot(**kwargs):
    from app.services.options.types import GreeksSnapshot

    defaults: dict[str, Decimal] = {
        "delta": Decimal("0.5"),
        "gamma": Decimal("0.028"),
        "theta": Decimal("-0.12"),
        "vega": Decimal("0.31"),
        "rho": Decimal("0.05"),
        "iv": Decimal("0.175"),
    }
    defaults.update(kwargs)
    return GreeksSnapshot(**defaults)


@pytest.mark.asyncio
async def test_upsert_guard_rejects_chain_browse_instrument() -> None:
    """upsert should refuse if instrument has no position or order today."""
    svc = _make_service()
    svc._has_position_or_order = AsyncMock(return_value=False)
    svc._db_upsert = AsyncMock()

    snap = _make_snapshot()
    await svc.upsert(instrument_id=42, greeks=snap)

    svc._db_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_writes_when_position_exists() -> None:
    """upsert should write when instrument has a position."""
    svc = _make_service()
    svc._has_position_or_order = AsyncMock(return_value=True)
    svc._db_upsert = AsyncMock()

    snap = _make_snapshot()
    await svc.upsert(instrument_id=42, greeks=snap)

    svc._db_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_greeks_clamping_applied_before_upsert() -> None:
    """GreeksSnapshot with out-of-range values should be clamped."""
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


@pytest.mark.asyncio
async def test_evict_stale_deletes_old_rows() -> None:
    """evict_stale should delete rows older than the threshold."""
    svc = _make_service()
    svc._db_delete_stale = AsyncMock(return_value=5)

    deleted = await svc.evict_stale(older_than=timedelta(minutes=5))
    assert deleted == 5
    svc._db_delete_stale.assert_called_once()
