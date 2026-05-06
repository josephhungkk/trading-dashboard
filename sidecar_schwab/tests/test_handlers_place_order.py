"""Phase 8a -- PlaceOrder live path: SIM, replay cache, REST, error map."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.client import SchwabHTTPError
from sidecar_schwab.handlers import BrokerServicer


def _build_servicer(
    *, with_client: bool = True, simulator=None, poller=None
) -> BrokerServicer:
    s = BrokerServicer()
    if with_client:
        s._client = MagicMock()
        s._client.hash_for = MagicMock(return_value="ACCT_HASH")
        s._client.ensure_fresh_token = AsyncMock()
        s._client.place_order = AsyncMock(return_value={"broker_order_id": "12345"})
    if simulator is not None:
        s._simulator = simulator
    if poller is not None:
        s._poller = poller
    return s


def _build_req(coid: str = "cli-1") -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="ACCT-1",
        client_order_id=coid,
        conid="AAPL",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="1",
    )


@pytest.mark.asyncio
async def test_place_order_live_returns_broker_order_id() -> None:
    poller = MagicMock()
    s = _build_servicer(poller=poller)
    rsp = await s.PlaceOrder(_build_req(), MagicMock())
    assert rsp.broker_order_id == "12345"
    assert rsp.status == "submitted"
    s._client.ensure_fresh_token.assert_awaited_once()
    poller.activate_fast.assert_called_once_with(account_number="ACCT-1")


@pytest.mark.asyncio
async def test_place_order_sim_routes_to_simulator() -> None:
    sim = MagicMock()
    sim.register = MagicMock(return_value="SIM-uuid7-xyz")
    s = _build_servicer(simulator=sim)
    rsp = await s.PlaceOrder(_build_req(coid="SIM-test-1"), MagicMock())
    assert rsp.broker_order_id == "SIM-uuid7-xyz"
    sim.register.assert_called_once()
    s._client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_order_replay_cache_returns_cached_result() -> None:
    s = _build_servicer()
    req = _build_req(coid="cli-2")
    rsp1 = await s.PlaceOrder(req, MagicMock())
    rsp2 = await s.PlaceOrder(req, MagicMock())
    assert rsp1.broker_order_id == rsp2.broker_order_id == "12345"
    s._client.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_place_order_429_aborts_resource_exhausted() -> None:
    s = _build_servicer()
    s._client.place_order = AsyncMock(
        side_effect=SchwabHTTPError(
            "rate limit",
            status_code=429,
            endpoint="/orders",
        )
    )
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    with pytest.raises(Exception, match="aborted"):
        await s.PlaceOrder(_build_req(coid="cli-3"), ctx)
    ctx.abort.assert_awaited_once()
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.RESOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_place_order_unconfigured_aborts_failed_precondition() -> None:
    s = BrokerServicer()  # no _client
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    with pytest.raises(Exception, match="aborted"):
        await s.PlaceOrder(_build_req(), ctx)
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.FAILED_PRECONDITION
