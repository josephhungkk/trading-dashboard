from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sidecar_ibkr import metrics
from sidecar_ibkr._generated.broker.v1 import broker_pb2
from sidecar_ibkr.handlers import BrokerHandlers
from sidecar_ibkr.pnl_cache import PnLCache


class CapturingEvent:
    def __init__(self) -> None:
        self.emitted: list[object] = []

    def emit(self, trade: object) -> None:
        self.emitted.append(trade)


@dataclass
class FakeIB:
    orderStatusEvent: CapturingEvent = field(default_factory=CapturingEvent)  # noqa: N815


def _handlers(ib: FakeIB) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        pnl_cache=PnLCache(ib),  # type: ignore[arg-type]
        label="ibgw_live_us",
        version="0.4.0+test",
        last_tick_ref={},
        simulator_only=True,
    )


def _metric_value() -> float:
    return metrics.broker_sim_cancel_echo_total.labels(label="ibgw_live_us")._value.get()


@pytest.mark.asyncio
async def test_cancel_sim_order_returns_accepted_false_when_unknown() -> None:
    ib = FakeIB()
    h = _handlers(ib)
    before = _metric_value()

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U1111111",
            broker_order_id="SIM-missing",
        ),
        context=object(),
    )

    assert response.accepted is False
    assert ib.orderStatusEvent.emitted == []
    assert _metric_value() == before


@pytest.mark.asyncio
async def test_cancel_sim_order_returns_accepted_true_and_removes_registry_entry() -> None:
    import asyncio

    ib = FakeIB()
    h = _handlers(ib)
    h._sim_orders["SIM-123"] = {
        "client_order_id": "client-order-1",
        "account_number": "U1111111",
    }
    queue: asyncio.Queue = asyncio.Queue()
    h._order_event_queues["U1111111"] = [queue]

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U1111111",
            broker_order_id="SIM-123",
        ),
        context=object(),
    )

    assert response.accepted is True
    assert "SIM-123" not in h._sim_orders
    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_cancel_sim_order_emits_synthetic_cancelled_trade_with_original_metadata() -> None:
    import asyncio

    ib = FakeIB()
    h = _handlers(ib)
    h._sim_orders["SIM-456"] = {
        "client_order_id": "client-order-456",
        "account_number": "U2222222",
    }
    queue: asyncio.Queue = asyncio.Queue()
    h._order_event_queues["U2222222"] = [queue]

    await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U2222222",
            broker_order_id="SIM-456",
        ),
        context=object(),
    )

    msg = queue.get_nowait()
    assert msg.broker_order_id == "SIM-456"
    assert msg.client_order_id == "client-order-456"
    assert msg.status == "cancelled"
    assert msg.kind == "status"
    assert msg.filled_qty == "0"
    assert msg.avg_fill_price == "0"


@pytest.mark.asyncio
async def test_cancel_sim_order_increments_cancel_echo_metric() -> None:
    ib = FakeIB()
    h = _handlers(ib)
    h._sim_orders["SIM-789"] = {
        "client_order_id": "client-order-789",
        "account_number": "U3333333",
    }
    before = _metric_value()

    response = await h.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="U3333333",
            broker_order_id="SIM-789",
        ),
        context=object(),
    )

    assert response.accepted is True
    assert _metric_value() == before + 1
