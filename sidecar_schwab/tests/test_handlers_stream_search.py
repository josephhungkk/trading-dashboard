"""Phase 8a — OrderEvent server-streaming + SearchContracts (5m cache)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


def _build_servicer(*, with_client: bool = True, poller=None):
    s = BrokerServicer()
    if with_client:
        s._client = MagicMock()
        s._client.ensure_fresh_token = AsyncMock()
        s._client.search_instruments = AsyncMock(
            return_value=[
                {
                    "symbol": "AAPL",
                    "description": "Apple Inc.",
                    "cusip": "037833100",
                    "exchange": "NASDAQ",
                    "assetType": "EQUITY",
                }
            ]
        )
    if poller is not None:
        s._poller = poller
    return s


@pytest.mark.asyncio
async def test_search_contracts_returns_contracts():
    s = _build_servicer()
    req = broker_pb2.SearchContractsRequest(query="AAPL")
    rsp = await s.SearchContracts(req, MagicMock())
    assert len(rsp.contracts) == 1
    assert rsp.contracts[0].symbol == "AAPL"


@pytest.mark.asyncio
async def test_search_contracts_caches_within_ttl():
    s = _build_servicer()
    req = broker_pb2.SearchContractsRequest(query="AAPL")
    await s.SearchContracts(req, MagicMock())
    await s.SearchContracts(req, MagicMock())
    s._client.search_instruments.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_contracts_empty_query_aborts_invalid_argument():
    s = _build_servicer()
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    req = broker_pb2.SearchContractsRequest(query="")
    with pytest.raises(Exception, match="aborted"):
        await s.SearchContracts(req, ctx)
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_order_event_yields_messages_from_fan_out():
    queue: asyncio.Queue = asyncio.Queue()
    fan_out = MagicMock()
    fan_out.subscribe = MagicMock(return_value=queue)
    fan_out.unsubscribe = MagicMock()
    poller = MagicMock()
    poller.fan_out_for = MagicMock(return_value=fan_out)
    s = _build_servicer(poller=poller)
    req = broker_pb2.AccountRef(account_number="ACCT-1")
    ctx = MagicMock()
    ctx.is_active = MagicMock(return_value=True)
    ev = broker_pb2.OrderEventMessage(
        broker_order_id="12345",
        client_order_id="cli-1",
        status="submitted",
        kind="status",
    )
    await queue.put(ev)
    await queue.put(None)
    yielded = []
    async for msg in s.OrderEvent(req, ctx):
        yielded.append(msg)
    assert len(yielded) == 1
    assert yielded[0].broker_order_id == "12345"
    fan_out.unsubscribe.assert_called_once_with(queue)


@pytest.mark.asyncio
async def test_order_event_unwired_aborts_unavailable():
    s = BrokerServicer()
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    req = broker_pb2.AccountRef(account_number="ACCT-1")

    async def _consume():
        async for _ in s.OrderEvent(req, ctx):
            pass

    with pytest.raises(Exception, match="aborted"):
        await _consume()
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.UNAVAILABLE
