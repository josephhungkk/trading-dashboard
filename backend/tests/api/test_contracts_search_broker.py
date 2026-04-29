"""Broker routing tests for GET /api/contracts/search."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.api import contracts as contracts_api
from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, require_admin_jwt
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


_APPLE = base.Contract(
    symbol="AAPL",
    exchange="SMART",
    currency="USD",
    asset_class="STOCK",
    conid="265598",
    local_symbol="AAPL",
)


class _Sidecar:
    def __init__(self, label: str, contracts: list[base.Contract] | None = None) -> None:
        self.label = label
        self.contracts = contracts or [_APPLE]
        self.call_count = 0

    async def search_contracts(self, *, query: str, asset_class: str = "") -> list[base.Contract]:
        self.call_count += 1
        return self.contracts


class _Registry:
    def __init__(self, healthy: list[_Sidecar], clients: dict[str, _Sidecar]) -> None:
        self.healthy = healthy
        self.clients = clients
        self.get_client_calls: list[str] = []

    async def healthy_clients(self) -> list[_Sidecar]:
        return self.healthy

    async def get_client(self, label: str) -> _Sidecar:
        self.get_client_calls.append(label)
        return self.clients[label]


@pytest.fixture
async def search_context() -> AsyncIterator[dict[str, Any]]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    default_sidecar = _Sidecar("isa-paper")
    registry = _Registry([default_sidecar], {"isa-paper": default_sidecar})

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_registry() -> _Registry:
        return registry

    async def override_redis() -> fakeredis.aioredis.FakeRedis:
        return redis

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[contracts_api.get_contracts_redis] = override_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "registry": registry, "redis": redis}
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_broker_invalid_returns_422(search_context: dict[str, Any]) -> None:
    client: AsyncClient = search_context["client"]

    resp = await client.get("/api/contracts/search", params={"q": "AAPL", "broker": "evil"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_broker_schwab_returns_503(search_context: dict[str, Any]) -> None:
    client: AsyncClient = search_context["client"]

    resp = await client.get("/api/contracts/search", params={"q": "AAPL", "broker": "schwab"})

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "86400"
    assert resp.json()["error"] == "schwab_not_yet_supported"


@pytest.mark.asyncio
async def test_search_broker_futu_routes_to_futu_client(search_context: dict[str, Any]) -> None:
    client: AsyncClient = search_context["client"]
    registry: _Registry = search_context["registry"]
    futu = _Sidecar("futu")
    registry.clients["futu"] = futu

    resp = await client.get("/api/contracts/search", params={"q": "AAPL", "broker": "futu"})

    assert resp.status_code == 200
    assert registry.get_client_calls == ["futu"]
    assert futu.call_count == 1


@pytest.mark.asyncio
async def test_search_broker_ibkr_picks_healthy_ibkr_label(search_context: dict[str, Any]) -> None:
    client: AsyncClient = search_context["client"]
    registry: _Registry = search_context["registry"]
    futu = _Sidecar("futu")
    normal = _Sidecar("normal-live")
    isa = _Sidecar("isa-paper")
    registry.healthy = [futu, normal, isa]
    registry.clients.update({"futu": futu, "normal-live": normal, "isa-paper": isa})

    resp = await client.get("/api/contracts/search", params={"q": "AAPL", "broker": "ibkr"})

    assert resp.status_code == 200
    assert normal.call_count == 1
    assert futu.call_count == 0
