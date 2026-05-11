"""Phase 8a — GET /api/brokers/{id}/capabilities.

Phase 10a D6: response shape pinned to BrokerCapabilitiesResponse
(broker_id + order_types[] + time_in_force[] + combos[]). The legacy
polymorphic flat-list / grouped-dict return was removed; FE consumers
now read `body["combos"]` unconditionally.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_schwab_capabilities_returns_full_universe(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    assert rsp.status_code == 200
    body = rsp.json()
    assert body["broker_id"] == "schwab"
    assert isinstance(body["order_types"], list)
    assert isinstance(body["time_in_force"], list)
    assert isinstance(body["combos"], list)
    order_types = {r["order_type"] for r in body["combos"]}
    tifs = {r["time_in_force"] for r in body["combos"]}
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
    combos = rsp.json()["combos"]
    supported = {(r["order_type"], r["time_in_force"]) for r in combos if r["supported"]}
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
    combos = rsp.json()["combos"]
    order_types_seen = {r["order_type"] for r in combos}
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


@pytest.mark.asyncio
async def test_response_includes_lookup_arrays(client: AsyncClient) -> None:
    """D6: order_types + time_in_force lookups round-trip with each request."""
    rsp = await client.get("/api/brokers/schwab/capabilities")
    body = rsp.json()
    ot_codes = {r["code"] for r in body["order_types"]}
    assert ot_codes >= {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}
    tif_codes = {r["code"] for r in body["time_in_force"]}
    assert tif_codes >= {"DAY", "GTC", "IOC", "FOK", "GTD"}
    # sort_order must be present + numeric for stable FE rendering.
    for r in body["order_types"]:
        assert isinstance(r["sort_order"], int)
    for r in body["time_in_force"]:
        assert isinstance(r["sort_order"], int)
