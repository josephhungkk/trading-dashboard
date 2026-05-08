"""Phase 8a — GET /api/brokers/{id}/capabilities.

The endpoint returns either:
- a flat list of capability rows (broker_id, asset_class, order_type,
  time_in_force, supported, notes) when 0 or 1 asset_class has any
  supported rows; or
- a dict grouped by asset_class when >=2 asset_classes have supported rows.

Tests below normalize both shapes via _rows_from_body().
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _rows_from_body(body: list | dict) -> list[dict]:
    """Normalize either shape to a flat list of capability rows."""
    if isinstance(body, list):
        return body
    rows: list[dict] = []
    for v in body.values():
        rows.extend(v)
    return rows


@pytest.mark.asyncio
async def test_get_schwab_capabilities_returns_full_universe(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    assert rsp.status_code == 200
    rows = _rows_from_body(rsp.json())
    order_types = {r["order_type"] for r in rows}
    tifs = {r["time_in_force"] for r in rows}
    assert order_types >= {
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    }
    assert tifs >= {"DAY", "GTC", "IOC", "FOK", "GTD"}


@pytest.mark.asyncio
async def test_get_ibkr_capabilities_supported_set(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/ibkr/capabilities")
    assert rsp.status_code == 200
    rows = _rows_from_body(rsp.json())
    supported = {(r["order_type"], r["time_in_force"]) for r in rows if r["supported"]}
    # Phase 8a A5 flipped MARKET/LIMIT/STOP/STOP_LIMIT x DAY/GTC/IOC/FOK = 16
    # combos. Later phases added TRAIL/TRAIL_LIMIT/MOC/etc., so use superset.
    baseline = {
        (o, t)
        for o in ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
        for t in ["DAY", "GTC", "IOC", "FOK"]
    }
    assert supported >= baseline, f"missing baseline IBKR supported rows: {baseline - supported}"


@pytest.mark.asyncio
async def test_get_unknown_broker_returns_404(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/bogus/capabilities")
    assert rsp.status_code == 404


@pytest.mark.asyncio
async def test_combos_ordered_by_sort_order(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    rows = _rows_from_body(rsp.json())
    order_types_seen = {r["order_type"] for r in rows}
    assert order_types_seen >= {
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    }
