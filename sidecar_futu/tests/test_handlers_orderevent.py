"""C8 — OrderEvent server-streaming handler + futu callback dispatch + H5 drop-pre-subscribe."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_orderevent_dispatches_callback_after_subscribe() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    handlers._client._order_event_queues.setdefault("12345678", []).append(queue)

    fake_order = {
        "order_id": 555,
        "code": "HK.00700",
        "order_status": "FILLED_ALL",
        "dealt_qty": "100",
        "dealt_avg_price": "350",
        "create_time": "2026-04-29 14:30:00",
        "updated_time": "2026-04-29 14:31:00",
        "remark": "cid-abc",
    }
    handlers._client._on_order_update("12345678", fake_order)

    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.broker_order_id == "555"
    assert event.status == "filled"
    assert event.client_order_id == "cid-abc"


@pytest.mark.asyncio
async def test_orderevent_pre_subscribe_dropped() -> None:
    """H5 — pre-subscribe callbacks are dropped, not buffered."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    fake_order = {
        "order_id": 1,
        "code": "HK.00700",
        "order_status": "SUBMITTED",
        "create_time": "2026-04-29 14:30:00",
        "updated_time": "2026-04-29 14:30:00",
        "remark": "",
    }
    handlers._client._on_order_update("12345678", fake_order)

    queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    handlers._client._order_event_queues.setdefault("12345678", []).append(queue)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_orderevent_stream_yields_subscribed_events() -> None:
    """OrderEvent server-streaming RPC yields events delivered to the queue."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    request = broker_pb2.AccountRef(account_number="12345678")
    stream = handlers.OrderEvent(request, context=None)

    async def _emit() -> None:
        await asyncio.sleep(0.01)
        handlers._client._on_order_update(
            "12345678",
            {
                "order_id": 777,
                "code": "HK.00700",
                "order_status": "FILLED_ALL",
                "dealt_qty": "100",
                "dealt_avg_price": "350",
                "create_time": "2026-04-29 14:30:00",
                "updated_time": "2026-04-29 14:31:00",
                "remark": "cid-stream",
            },
        )

    emit_task = asyncio.create_task(_emit())
    event = await asyncio.wait_for(stream.__anext__(), timeout=1)
    await emit_task
    assert event.broker_order_id == "777"
    assert event.client_order_id == "cid-stream"

    await stream.aclose()


@pytest.mark.asyncio
async def test_orderevent_stream_unregisters_queue_on_close() -> None:
    """Closing the stream removes the queue from _order_event_queues."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    request = broker_pb2.AccountRef(account_number="22222222")
    stream = handlers.OrderEvent(request, context=None)

    async def _emit_then_close() -> None:
        await asyncio.sleep(0.01)
        handlers._client._on_order_update(
            "22222222",
            {
                "order_id": 1,
                "code": "HK.00700",
                "order_status": "SUBMITTED",
                "create_time": "2026-04-29 14:30:00",
                "updated_time": "2026-04-29 14:30:00",
                "remark": "",
            },
        )

    emit_task = asyncio.create_task(_emit_then_close())
    await asyncio.wait_for(stream.__anext__(), timeout=1)
    await emit_task
    await stream.aclose()

    assert handlers._client._order_event_queues.get("22222222", []) == []
