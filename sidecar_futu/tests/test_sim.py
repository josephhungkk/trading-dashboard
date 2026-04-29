"""C7 — SIM mode with per-account synthetic event queues."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _place_req(client_order_id: str = "cid-abc") -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="12345678",
        client_order_id=client_order_id,
        conid="HK.00700",
        side="BUY",
        order_type="LIMIT",
        qty="100",
        limit_price="350.00",
        tif="DAY",
    )


@pytest.mark.asyncio
async def test_sim_place_returns_sim_prefix_and_dispatches() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    handlers._client._order_event_queues["12345678"] = [queue]

    resp = await handlers.PlaceOrder(_place_req(), context=None)
    assert resp.broker_order_id.startswith("SIM-")
    assert resp.status == "submitted"

    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.broker_order_id == resp.broker_order_id
    assert event.status == "submitted"


@pytest.mark.asyncio
async def test_sim_cancel_emits_synthetic_event() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    handlers._client._order_event_queues["12345678"] = [queue]

    place_resp = await handlers.PlaceOrder(_place_req(), context=None)
    sim_id = place_resp.broker_order_id
    _ = await queue.get()  # drain place event

    cancel_resp = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="12345678", broker_order_id=sim_id
        ),
        context=None,
    )
    assert cancel_resp.accepted is True

    cancel_event = await asyncio.wait_for(queue.get(), timeout=1)
    assert cancel_event.broker_order_id == sim_id
    assert cancel_event.status == "cancelled"


@pytest.mark.asyncio
async def test_sim_cancel_unknown_id_rejects() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    handlers._client._order_event_queues["12345678"] = [asyncio.Queue()]

    cancel_resp = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="12345678", broker_order_id="SIM-not-issued"
        ),
        context=None,
    )
    assert cancel_resp.accepted is False


@pytest.mark.asyncio
async def test_sim_cancel_non_sim_id_rejects() -> None:
    """Real broker-order-ids leaking into SIM mode must reject, not no-op."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    handlers._client._order_event_queues["12345678"] = [asyncio.Queue()]

    cancel_resp = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="12345678", broker_order_id="999111"
        ),
        context=None,
    )
    assert cancel_resp.accepted is False


@pytest.mark.asyncio
async def test_sim_dispatch_to_multiple_queues_per_account() -> None:
    """Two subscribers on the same account both receive the event."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)
    q1: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    q2: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue()
    handlers._client._order_event_queues["12345678"] = [q1, q2]

    resp = await handlers.PlaceOrder(_place_req(), context=None)

    e1 = await asyncio.wait_for(q1.get(), timeout=1)
    e2 = await asyncio.wait_for(q2.get(), timeout=1)
    assert e1.broker_order_id == resp.broker_order_id
    assert e2.broker_order_id == resp.broker_order_id


@pytest.mark.asyncio
async def test_sim_no_subscribers_does_not_crash() -> None:
    """Place with zero subscribers still returns; the event is dropped silently."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)

    resp = await handlers.PlaceOrder(_place_req(), context=None)
    assert resp.broker_order_id.startswith("SIM-")
    assert resp.status == "submitted"
