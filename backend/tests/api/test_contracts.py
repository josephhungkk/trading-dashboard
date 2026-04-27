"""Tests for GET /api/contracts/search."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, require_admin_jwt
from app.main import app
from app.services.brokers import BrokerSidecarUnavailable


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
    def __init__(
        self,
        contracts: list[base.Contract] | None = None,
        *,
        raise_unavailable: bool = False,
    ) -> None:
        self.contracts = contracts or [_APPLE]
        self.call_count = 0
        self.raise_unavailable = raise_unavailable

    async def search_contracts(self, *, query: str, asset_class: str = "") -> list[base.Contract]:
        self.call_count += 1
        if self.raise_unavailable:
            raise BrokerSidecarUnavailable("isa-paper down")
        return self.contracts


class _Registry:
    def __init__(self, sidecar: _Sidecar) -> None:
        self.sidecar = sidecar

    async def healthy_clients(self) -> list[_Sidecar]:
        return [self.sidecar]

    async def get_client(self, label: str) -> _Sidecar:
        return self.sidecar


def _make_app_context(
    sidecar: _Sidecar,
    redis: fakeredis.aioredis.FakeRedis,
) -> None:
    from app.api import contracts as contracts_api

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="user", claims={})

    async def override_registry() -> _Registry:
        return _Registry(sidecar)

    async def override_redis() -> fakeredis.aioredis.FakeRedis:
        return redis

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[contracts_api.get_contracts_redis] = override_redis


@pytest.fixture
async def search_client() -> AsyncIterator[dict[str, Any]]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sidecar = _Sidecar()
    _make_app_context(sidecar, redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "sidecar": sidecar, "redis": redis}
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_forwards_to_one_healthy_sidecar(
    search_client: dict[str, Any],
) -> None:
    """Healthy path — sidecar called once, 200 response with contract list."""
    client: AsyncClient = search_client["client"]
    sidecar: _Sidecar = search_client["sidecar"]

    resp = await client.get("/api/contracts/search", params={"q": "AAPL", "asset_class": "STK"})

    assert resp.status_code == 200
    body = resp.json()
    assert "contracts" in body
    assert len(body["contracts"]) == 1
    assert body["contracts"][0]["symbol"] == "AAPL"
    assert sidecar.call_count == 1


@pytest.mark.asyncio
async def test_search_caches_redis_5min_ttl(
    search_client: dict[str, Any],
) -> None:
    """Second identical request is served from Redis cache; sidecar called only once."""
    client: AsyncClient = search_client["client"]
    sidecar: _Sidecar = search_client["sidecar"]

    params = {"q": "AAPL", "asset_class": "STK"}

    resp1 = await client.get("/api/contracts/search", params=params)
    resp2 = await client.get("/api/contracts/search", params=params)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert sidecar.call_count == 1, "sidecar should be called only once (cache hit on 2nd request)"


@pytest.mark.asyncio
async def test_search_rate_limits_5_per_sec_per_user(
    search_client: dict[str, Any],
) -> None:
    """6th request within the same 1-second window returns 429."""
    from fakeredis.aioredis import FakeRedis

    redis = FakeRedis(decode_responses=True)
    sidecar = _Sidecar()
    _make_app_context(sidecar, redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = []
        for _ in range(6):
            resp = await client.get(
                "/api/contracts/search", params={"q": "TSLA", "asset_class": ""}
            )
            responses.append(resp.status_code)

    app.dependency_overrides.clear()

    ok_responses = [s for s in responses if s == 200]
    too_many = [s for s in responses if s == 429]
    assert len(ok_responses) == 5, f"expected 5 ok, got: {responses}"
    assert len(too_many) == 1, f"expected 1 rate-limit, got: {responses}"


@pytest.mark.asyncio
async def test_search_propagates_sidecar_503(
    search_client: dict[str, Any],
) -> None:
    """When sidecar raises BrokerSidecarUnavailable, endpoint returns 503."""
    from fakeredis.aioredis import FakeRedis

    redis = FakeRedis(decode_responses=True)
    sidecar = _Sidecar(raise_unavailable=True)
    _make_app_context(sidecar, redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/contracts/search", params={"q": "AAPL", "asset_class": ""})

    app.dependency_overrides.clear()

    assert resp.status_code == 503
    body = resp.json()
    assert "broker_maintenance" in body or "error" in body
