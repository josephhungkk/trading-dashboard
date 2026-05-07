"""OrderEvent stream coverage for Alpaca trade RPCs."""

from __future__ import annotations

import os
from types import SimpleNamespace

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import handlers
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise grpc.RpcError(details)


@pytest.fixture(autouse=True)
def clear_stream_state() -> None:
    handlers._ORDER_EVENT_QUEUES.clear()
    handlers._ORDER_EVENT_SUBSCRIPTIONS.clear()
    handlers._TRADING_STREAM_COUNTS.clear()
    yield
    handlers._ORDER_EVENT_QUEUES.clear()
    handlers._ORDER_EVENT_SUBSCRIPTIONS.clear()
    handlers._TRADING_STREAM_COUNTS.clear()


@pytest.mark.asyncio
async def test_subscribe_enqueues_trade_update_into_queue() -> None:
    svc = AlpacaServicer()
    update = SimpleNamespace(
        event="fill",
        order=SimpleNamespace(
            id="order-123",
            client_order_id="cid-1",
            status=SimpleNamespace(value="filled"),
            filled_qty="1",
            filled_avg_price="100.00",
        ),
    )

    svc._enqueue_order_event("acct-1", update)
    message = await handlers._ORDER_EVENT_QUEUES["acct-1"].get()

    assert message.broker_order_id == "order-123"
    assert message.client_order_id == "cid-1"
    assert message.status == "filled"


@pytest.mark.asyncio
async def test_order_event_stream_yields_queue_events() -> None:
    svc = AlpacaServicer()
    handlers._ORDER_EVENT_SUBSCRIPTIONS["acct-1"] = object()
    queue = svc._order_event_queue("acct-1")
    queue.put_nowait(broker_pb2.OrderEventMessage(broker_order_id="order-123"))

    stream = svc.OrderEvent(
        broker_pb2.AccountRef(account_number="acct-1"),
        FakeContext(),
    )
    message = await stream.__anext__()
    await stream.aclose()

    assert message.broker_order_id == "order-123"


@pytest.mark.asyncio
async def test_trading_stream_cap_five_allows_fifth_and_rejects_sixth() -> None:
    svc = AlpacaServicer()
    handlers._ORDER_EVENT_SUBSCRIPTIONS["acct-1"] = object()
    streams = []
    for index in range(5):
        svc._order_event_queue("acct-1").put_nowait(
            broker_pb2.OrderEventMessage(broker_order_id=f"order-{index}"),
        )
        stream = svc.OrderEvent(
            broker_pb2.AccountRef(account_number="acct-1"),
            FakeContext(),
        )
        await stream.__anext__()
        streams.append(stream)

    rejected = svc.OrderEvent(
        broker_pb2.AccountRef(account_number="acct-1"),
        FakeContext(),
    )
    with pytest.raises(grpc.RpcError, match="trading_stream_cap_5"):
        await rejected.__anext__()

    for stream in streams:
        await stream.aclose()
