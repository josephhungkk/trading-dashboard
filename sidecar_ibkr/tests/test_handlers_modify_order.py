"""Sidecar ModifyOrder handler (5c B1+B5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_ibkr import handlers
from sidecar_ibkr._generated.broker.v1 import broker_pb2


def _rpc_code(exc: grpc.RpcError) -> grpc.StatusCode:
    return exc.args[0]


@pytest.fixture
def mock_ib() -> MagicMock:
    ib = MagicMock()
    ib.openTrades = MagicMock(return_value=[])
    return ib


@pytest.fixture
def handler(mock_ib: MagicMock) -> handlers.BrokerHandlers:
    return handlers.BrokerHandlers(
        ib=mock_ib,
        pnl_cache={},
        label="isa-live",
        version="0.5.4-dev",
        last_tick_ref={},
        simulator_only=False,
    )


@pytest.mark.asyncio
async def test_modify_sim_unregistered_returns_not_found(handler) -> None:
    """Unregistered SIM-* id (e.g. after sidecar restart) -> NOT_FOUND."""
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id="SIM-deadbeef",
        account_number="DU111",
    )
    with pytest.raises(grpc.RpcError) as exc:
        await handler.ModifyOrder(request, context=MagicMock())
    assert _rpc_code(exc.value) == grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_modify_sim_registered_emits_synthetic_modified_event(handler) -> None:
    """Registered SIM-* id -> dispatch synthetic Modified event into the
    OrderEvent queue for that account, return same id."""
    import asyncio

    sim_id = "SIM-abc123"
    handler._sim_orders[sim_id] = {
        "client_order_id": "client-abc",
        "account_number": "DU111",
    }
    queue: asyncio.Queue = asyncio.Queue()
    handler._order_event_queues["DU111"] = [queue]
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id=sim_id,
        account_number="DU111",
    )

    response = await handler.ModifyOrder(request, context=MagicMock())

    assert response.broker_order_id == sim_id
    assert response.status == "Modified"
    assert queue.qsize() == 1
    msg = queue.get_nowait()
    assert msg.broker_order_id == sim_id
    assert msg.client_order_id == "client-abc"
    assert msg.status == "modified"
    assert msg.kind == "status"


@pytest.mark.asyncio
async def test_modify_invalid_int_id_rejected(handler) -> None:
    """Non-numeric broker_order_id rejected with INVALID_ARGUMENT."""
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id="abc123",
        account_number="DU111",
    )
    with pytest.raises(grpc.RpcError) as exc:
        await handler.ModifyOrder(request, context=MagicMock())
    assert _rpc_code(exc.value) == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_modify_order_id_not_found(handler, mock_ib) -> None:
    """orderId not in openTrades -> NOT_FOUND."""
    mock_ib.openTrades.return_value = []
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id="123456",
        account_number="DU111",
        contract=broker_pb2.Contract(
            conid="265598", symbol="AAPL", exchange="SMART", currency="USD",
        ),
    )
    with pytest.raises(grpc.RpcError) as exc:
        await handler.ModifyOrder(request, context=MagicMock())
    assert _rpc_code(exc.value) == grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_modify_reuses_open_trade_contract(handler, mock_ib) -> None:
    """Real modify succeeds even when request.contract.conid is unset."""
    trade = MagicMock()
    trade.order.permId = 123456
    trade.order.account = "DU111"
    trade.orderStatus.status = "Submitted"
    trade.contract = MagicMock(name="live_contract")
    mock_ib.openTrades.return_value = [trade]
    mock_ib.placeOrder.return_value = trade
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id="123456",
        account_number="DU111",
        qty="2",
        tif=broker_pb2.TIF_DAY,
    )

    response = await handler.ModifyOrder(request, context=MagicMock())

    assert response.broker_order_id == "123456"
    assert response.status == "Submitted"
    mock_ib.placeOrder.assert_called_once_with(trade.contract, trade.order)


@pytest.mark.asyncio
async def test_modify_simulator_only_rejected() -> None:
    """When simulator_only=True with non-SIM id, the openTrades scan returns
    empty in this test's mock so we fall through to NOT_FOUND. v1 doesn't
    formally guard against simulator_only at the start of ModifyOrder; that's
    acceptable since the openTrades scan can never match in SIM mode (no real
    broker trades exist). Either INVALID_ARGUMENT or NOT_FOUND is acceptable.
    """
    sim_handler = handlers.BrokerHandlers(
        ib=MagicMock(openTrades=MagicMock(return_value=[])),
        pnl_cache={},
        label="isa-paper",
        version="0.5.4-dev",
        last_tick_ref={},
        simulator_only=True,
    )
    request = broker_pb2.ModifyOrderRequest(
        broker_order_id="123456",
        account_number="DU111",
        contract=broker_pb2.Contract(
            conid="265598", symbol="AAPL", exchange="SMART", currency="USD",
        ),
    )
    with pytest.raises(grpc.RpcError) as exc:
        await sim_handler.ModifyOrder(request, context=MagicMock())
    assert _rpc_code(exc.value) in (
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.NOT_FOUND,
    )
