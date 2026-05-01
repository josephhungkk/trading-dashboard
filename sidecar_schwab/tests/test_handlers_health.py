"""Phase 7a B5 - Health.gateway_connected reflects token freshness AND hashes."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.auth import TokenCache
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_health_disconnected_before_configure():
    servicer = BrokerServicer()
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False
    assert resp.broker_id == "schwab"


@pytest.mark.asyncio
async def test_health_disconnected_when_token_stale():
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A",
        refresh_token="R",
        access_issued_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {"123": "HASH"}
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False


@pytest.mark.asyncio
async def test_health_disconnected_when_no_hashes():
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A",
        refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {}
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is False


@pytest.mark.asyncio
async def test_health_connected_when_token_fresh_and_hashes_present():
    servicer = BrokerServicer()
    servicer._token_cache = TokenCache(refresh_client=MagicMock())
    servicer._token_cache.set_tokens(
        access_token="A",
        refresh_token="R",
        access_issued_at=datetime.now(timezone.utc),
    )
    servicer._client = MagicMock()
    servicer._client._account_hashes = {"123": "HASH"}
    servicer._configured_at = datetime.now(timezone.utc)
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.Health(pb.HealthRequest(), ctx)
    assert resp.gateway_connected is True
    assert resp.broker_id == "schwab"
