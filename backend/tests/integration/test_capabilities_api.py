"""Phase 8a — GET /api/brokers/{id}/capabilities."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_schwab_capabilities_returns_full_universe(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    assert rsp.status_code == 200
    body = rsp.json()
    assert {r["code"] for r in body["order_types"]} == {
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
    assert {r["code"] for r in body["time_in_force"]} == {"DAY", "GTC", "IOC", "FOK", "GTD"}
    assert len(body["combos"]) == 50
    # Pre-flip (A5 not yet run): zero supported for Schwab.
    supported = [c for c in body["combos"] if c["supported"]]
    assert supported == []


@pytest.mark.asyncio
async def test_get_ibkr_capabilities_supported_set(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/ibkr/capabilities")
    assert rsp.status_code == 200
    body = rsp.json()
    supported = {(c["order_type"], c["time_in_force"]) for c in body["combos"] if c["supported"]}
    assert supported == {
        (o, t)
        for o in ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
        for t in ["DAY", "GTC", "IOC", "FOK"]
    }


@pytest.mark.asyncio
async def test_get_unknown_broker_returns_404(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/bogus/capabilities")
    assert rsp.status_code == 404


@pytest.mark.asyncio
async def test_combos_ordered_by_sort_order(client: AsyncClient) -> None:
    rsp = await client.get("/api/brokers/schwab/capabilities")
    body = rsp.json()
    type_codes = [r["code"] for r in body["order_types"]]
    assert type_codes == [
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
    ]
