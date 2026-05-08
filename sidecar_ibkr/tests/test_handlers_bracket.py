"""Sidecar PlaceBracket handler (5c B2+B6)."""

from __future__ import annotations

from unittest.mock import MagicMock

import grpc
import pytest

from sidecar_ibkr import handlers
from sidecar_ibkr._generated.broker.v1 import broker_pb2


@pytest.fixture
def mock_ib() -> MagicMock:
    """ib_async.IB stand-in. placeOrder.side_effect mutates `order.orderId`
    synchronously to mirror ib_async's behavior, and returns a Trade-shaped
    mock with a unique permId."""
    ib = MagicMock()
    counter = {"n": 1000}

    def fake_place_order(contract, order):
        counter["n"] += 1
        order.orderId = counter["n"]  # ib_async assigns synchronously
        trade = MagicMock()
        trade.order = order
        trade.orderStatus.status = "Submitted"
        return trade

    ib.placeOrder.side_effect = fake_place_order
    return ib


def _make_request(
    *, has_sl: bool, has_tp: bool, parent_qty: str = "100"
) -> broker_pb2.PlaceBracketRequest:
    parent = broker_pb2.PlaceOrderRequest(
        client_order_id="parent-uuid",
        account_number="DU111",
        conid="265598",
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty=parent_qty,
        limit_price="150",
    )
    sl = (
        broker_pb2.PlaceOrderRequest(
            client_order_id="sl-uuid",
            account_number="DU111",
            conid="265598",
            side="SELL",
            order_type="STOP",
            tif="DAY",
            qty=parent_qty,
            stop_price="145",
        )
        if has_sl
        else broker_pb2.PlaceOrderRequest()
    )
    tp = (
        broker_pb2.PlaceOrderRequest(
            client_order_id="tp-uuid",
            account_number="DU111",
            conid="265598",
            side="SELL",
            order_type="LIMIT",
            tif="DAY",
            qty=parent_qty,
            limit_price="160",
        )
        if has_tp
        else broker_pb2.PlaceOrderRequest()
    )
    return broker_pb2.PlaceBracketRequest(
        parent=parent,
        stop_loss=sl,
        take_profit=tp,
        oca_group="BRK-test",
        has_stop_loss=has_sl,
        has_take_profit=has_tp,
    )


@pytest.fixture
def real_handler(mock_ib: MagicMock) -> handlers.BrokerHandlers:
    h = handlers.BrokerHandlers(
        ib=mock_ib,
        pnl_cache={},
        label="isa-live",
        version="0.5.4-dev",
        last_tick_ref={},
        simulator_only=False,
    )
    # Stub _resolve_contract to return a MagicMock contract.
    async def _stub_contract(_conid):
        return MagicMock()

    h._resolve_contract = _stub_contract  # type: ignore[method-assign]
    return h


@pytest.mark.asyncio
async def test_place_bracket_full_three_legs(real_handler, mock_ib) -> None:
    """Full bracket: parent + SL + TP - three placeOrder calls."""
    response = await real_handler.PlaceBracket(
        _make_request(has_sl=True, has_tp=True), context=MagicMock()
    )
    assert mock_ib.placeOrder.call_count == 3
    assert response.parent_broker_order_id != ""
    assert response.stop_loss_broker_order_id != ""
    assert response.take_profit_broker_order_id != ""


@pytest.mark.asyncio
async def test_place_bracket_parent_id_wired_on_children(real_handler, mock_ib) -> None:
    """Children's parentId is set to parent's orderId."""
    await real_handler.PlaceBracket(
        _make_request(has_sl=True, has_tp=True), context=MagicMock()
    )
    parent_order = mock_ib.placeOrder.call_args_list[0][0][1]
    parent_order_id = parent_order.orderId
    sl_order = mock_ib.placeOrder.call_args_list[1][0][1]
    tp_order = mock_ib.placeOrder.call_args_list[2][0][1]
    assert sl_order.parentId == parent_order_id
    assert tp_order.parentId == parent_order_id


@pytest.mark.asyncio
async def test_place_bracket_transmit_only_on_last_child(
    real_handler, mock_ib
) -> None:
    """Parent + first child = transmit=False; last child = transmit=True."""
    await real_handler.PlaceBracket(
        _make_request(has_sl=True, has_tp=True), context=MagicMock()
    )
    parent_order = mock_ib.placeOrder.call_args_list[0][0][1]
    sl_order = mock_ib.placeOrder.call_args_list[1][0][1]
    tp_order = mock_ib.placeOrder.call_args_list[2][0][1]
    assert parent_order.transmit is False
    assert sl_order.transmit is False
    assert tp_order.transmit is True


@pytest.mark.asyncio
async def test_place_bracket_oca_group_propagates(real_handler, mock_ib) -> None:
    """Both children share the same oca_group + ocaType=1."""
    await real_handler.PlaceBracket(
        _make_request(has_sl=True, has_tp=True), context=MagicMock()
    )
    sl_order = mock_ib.placeOrder.call_args_list[1][0][1]
    tp_order = mock_ib.placeOrder.call_args_list[2][0][1]
    assert sl_order.ocaGroup == "BRK-test"
    assert tp_order.ocaGroup == "BRK-test"
    assert sl_order.ocaType == 1
    assert tp_order.ocaType == 1


@pytest.mark.asyncio
async def test_place_bracket_sim_mode_mints_three_uuids() -> None:
    """In simulator mode: 3 SIM- uuids minted, registered in _sim_orders, no IB calls."""
    sim_ib = MagicMock()
    sim_handler = handlers.BrokerHandlers(
        ib=sim_ib,
        pnl_cache={},
        label="isa-paper",
        version="0.5.4-dev",
        last_tick_ref={},
        simulator_only=True,
    )

    async def _stub(_c):
        return MagicMock()

    sim_handler._resolve_contract = _stub  # type: ignore[method-assign]

    response = await sim_handler.PlaceBracket(
        _make_request(has_sl=True, has_tp=True), context=MagicMock()
    )
    sim_ib.placeOrder.assert_not_called()
    assert response.parent_broker_order_id.startswith("SIM-")
    assert response.stop_loss_broker_order_id.startswith("SIM-")
    assert response.take_profit_broker_order_id.startswith("SIM-")
    assert len(sim_handler._sim_orders) == 3


@pytest.mark.asyncio
async def test_place_bracket_child_failure_cancels_parent_and_placed_child(
    real_handler, mock_ib
) -> None:
    calls = {"n": 0}

    def fake_place_order(contract, order):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("tp rejected")
        order.orderId = 1000 + calls["n"]
        trade = MagicMock()
        trade.order = order
        trade.order.permId = 2000 + calls["n"]
        trade.orderStatus.status = "Submitted"
        return trade

    mock_ib.placeOrder.side_effect = fake_place_order

    with pytest.raises(grpc.RpcError) as exc:
        await real_handler.PlaceBracket(_make_request(has_sl=True, has_tp=True), MagicMock())

    assert exc.value.args[0] == grpc.StatusCode.INTERNAL
    assert mock_ib.cancelOrder.call_count == 2
