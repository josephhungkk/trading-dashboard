"""Phase 8a C4 -- CancelOrder and ModifyOrder live paths."""

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
        s._client.cancel_order = AsyncMock()
        s._client.replace_order = AsyncMock(return_value={"broker_order_id": "99999"})
    if simulator is not None:
        s._simulator = simulator
    if poller is not None:
        s._poller = poller
    return s


def _cancel_req(order_id: str = "12345") -> broker_pb2.CancelOrderRequest:
    return broker_pb2.CancelOrderRequest(
        account_number="ACCT-1",
        broker_order_id=order_id,
    )


def _modify_req(order_id: str = "12345") -> broker_pb2.ModifyOrderRequest:
    return broker_pb2.ModifyOrderRequest(
        account_number="ACCT-1",
        broker_order_id=order_id,
        contract=broker_pb2.Contract(symbol="AAPL", conid="AAPL"),
        side=broker_pb2.OrderSide.BUY,
        order_type=broker_pb2.OrderType.ORDER_TYPE_LIMIT,
        tif=broker_pb2.TimeInForce.TIF_DAY,
        qty="2",
        limit_price=broker_pb2.Money(value="185.50", currency="USD"),
        client_order_id="cli-mod-1",
    )


@pytest.mark.asyncio
async def test_cancel_order_live_returns_accepted() -> None:
    poller = MagicMock()
    s = _build_servicer(poller=poller)
    rsp = await s.CancelOrder(_cancel_req(), MagicMock())
    assert rsp.accepted is True
    s._client.ensure_fresh_token.assert_awaited_once()
    s._client.cancel_order.assert_awaited_once_with(
        account_hash="ACCT_HASH",
        order_id="12345",
    )
    poller.activate_fast.assert_called_once_with(account_number="ACCT-1")


@pytest.mark.asyncio
async def test_cancel_order_sim_routes_to_simulator() -> None:
    sim = MagicMock()
    s = _build_servicer(simulator=sim)
    rsp = await s.CancelOrder(_cancel_req("SIM-order-1"), MagicMock())
    assert rsp.accepted is True
    sim.cancel.assert_called_once_with("SIM-order-1")
    s._client.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_order_429_aborts_resource_exhausted() -> None:
    s = _build_servicer()
    s._client.cancel_order = AsyncMock(
        side_effect=SchwabHTTPError(
            "rate limit",
            status_code=429,
            endpoint="/orders",
        )
    )
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    with pytest.raises(Exception, match="aborted"):
        await s.CancelOrder(_cancel_req(), ctx)
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.RESOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_modify_order_live_replaces_and_returns_new_order_id() -> None:
    poller = MagicMock()
    s = _build_servicer(poller=poller)
    rsp = await s.ModifyOrder(_modify_req(), MagicMock())
    assert rsp.broker_order_id == "99999"
    assert rsp.status == "submitted"
    assert rsp.parent_broker_order_id == "12345"
    s._client.ensure_fresh_token.assert_awaited_once()
    s._client.replace_order.assert_awaited_once()
    kwargs = s._client.replace_order.await_args.kwargs
    assert kwargs["account_hash"] == "ACCT_HASH"
    assert kwargs["order_id"] == "12345"
    assert kwargs["payload"]["orderType"] == "LIMIT"
    assert kwargs["payload"]["duration"] == "DAY"
    assert kwargs["payload"]["orderLegCollection"][0]["instruction"] == "BUY"
    assert kwargs["payload"]["orderLegCollection"][0]["quantity"] == "2"
    assert kwargs["payload"]["orderLegCollection"][0]["instrument"]["symbol"] == "AAPL"
    assert kwargs["payload"]["price"] == "185.50"
    poller.activate_fast.assert_called_once_with(account_number="ACCT-1")


@pytest.mark.asyncio
async def test_modify_order_sim_routes_to_simulator() -> None:
    sim = MagicMock()
    sim.modify = MagicMock(return_value="SIM-order-2")
    s = _build_servicer(simulator=sim)
    req = _modify_req("SIM-order-1")
    rsp = await s.ModifyOrder(req, MagicMock())
    assert rsp.broker_order_id == "SIM-order-2"
    assert rsp.parent_broker_order_id == "SIM-order-1"
    sim.modify.assert_called_once_with("SIM-order-1", req)
    s._client.replace_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_modify_order_unconfigured_aborts_failed_precondition() -> None:
    s = BrokerServicer()
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))
    with pytest.raises(Exception, match="aborted"):
        await s.ModifyOrder(_modify_req(), ctx)
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.FAILED_PRECONDITION
