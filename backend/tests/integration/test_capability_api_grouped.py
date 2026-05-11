"""Phase 10a D6: previously tested the polymorphic grouped-dict / flat-list
shapes that the endpoint returned depending on supported-asset-class count.
That polymorphism was removed; the endpoint now always returns the
structured BrokerCapabilitiesResponse. These tests now verify that
asset_class is preserved inside combos and that the ?asset_class= filter
narrows the combos array."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_multi_asset_broker_combos_include_all_asset_classes(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/alpaca/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    asset_classes = {row["asset_class"] for row in body["combos"]}
    assert {"STOCK", "CRYPTO"} <= asset_classes


@pytest.mark.asyncio
async def test_single_asset_broker_returns_structured_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/schwab/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert body["broker_id"] == "schwab"
    assert isinstance(body["combos"], list)
    assert isinstance(body["order_types"], list)
    assert isinstance(body["time_in_force"], list)


@pytest.mark.asyncio
async def test_query_param_filters_combos_by_asset_class(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/alpaca/capabilities?asset_class=STOCK")
    assert resp.status_code == 200
    body = resp.json()
    assert all(row["asset_class"] == "STOCK" for row in body["combos"])
    # Lookup arrays are global and always return the full set.
    assert len(body["order_types"]) > 0
    assert len(body["time_in_force"]) > 0
