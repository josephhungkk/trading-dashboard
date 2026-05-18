"""Tests for GET /api/algo/capabilities and /api/algo/schemas."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_capabilities_ibkr_stock(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/capabilities/ibkr/STOCK")
    assert resp.status_code == 200
    data = resp.json()
    strategies = [s["strategy"] for s in data["strategies"]]
    assert "TWAP" in strategies
    assert "ADAPTIVE" in strategies


async def test_get_capabilities_schwab_stock_empty(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/capabilities/schwab/STOCK")
    assert resp.status_code == 200
    assert resp.json()["strategies"] == []


async def test_get_schemas(test_client_admin: AsyncClient):
    resp = await test_client_admin.get("/api/algo/schemas")
    assert resp.status_code == 200
    schemas = resp.json()["schemas"]
    assert "ADAPTIVE" in schemas
    assert "DARK_ICE" in schemas


async def test_get_capabilities_requires_auth(test_client_no_auth: AsyncClient):
    resp = await test_client_no_auth.get("/api/algo/capabilities/ibkr/STOCK")
    assert resp.status_code == 401
