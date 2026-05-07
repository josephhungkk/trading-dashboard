from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_grouped_response_for_multi_asset_broker(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/alpaca/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "STOCK" in data
    assert "CRYPTO" in data


@pytest.mark.asyncio
async def test_flat_response_for_single_asset_broker(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/schwab/capabilities")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_query_param_returns_flat_list(client: AsyncClient) -> None:
    resp = await client.get("/api/brokers/alpaca/capabilities?asset_class=STOCK")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert all(row["asset_class"] == "STOCK" for row in data)
