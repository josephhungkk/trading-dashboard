"""Phase 8b T-O.9 -- assert Futu sidecar surfaces support orchestrated OCO.

Futu has NO native OCO.  The backend orchestrator drives the OCO state machine
by:
  1. Calling PlaceOrder twice (once per leg)
  2. Subscribing to OrderEvent stream
  3. On a fill event, calling CancelOrder on the surviving leg

These tests verify each surface exists and behaves correctly.  No new sidecar
code is required: PlaceOrder, CancelOrder, and the OrderEvent server-streaming
RPC were all shipped in T-F.1 / C8 (commits 279376d + handlers.py).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _place_req(
    client_order_id: str = "cid-leg-A",
    account_number: str = "12345678",
    side: str = "BUY",
    limit_price: str = "350.00",
) -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number=account_number,
        client_order_id=client_order_id,
        conid="HK.00700",
        side=side,
        order_type="LIMIT",
        qty="100",
        limit_price=limit_price,
        tif="DAY",
    )


def _cancel_req(
    broker_order_id: str,
    account_number: str = "12345678",
) -> broker_pb2.CancelOrderRequest:
    return broker_pb2.CancelOrderRequest(
        account_number=account_number,
        broker_order_id=broker_order_id,
    )


def _fake_fill_row(order_id: int, client_order_id: str = "") -> dict:
    return {
        "order_id": order_id,
        "code": "HK.00700",
        "order_status": "FILLED_ALL",
        "dealt_qty": "100",
        "dealt_avg_price": "350.50",
        "create_time": "2026-05-06 10:00:00",
        "updated_time": "2026-05-06 10:00:01",
        "remark": client_order_id,
    }


# ---------------------------------------------------------------------------
# T-O.9-1: PlaceOrder returns distinct order_ids for both OCO legs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_returns_order_id_for_both_oco_legs() -> None:
    """PlaceOrder must return a usable order_id (not empty) for both OCO legs.

    The orchestrator calls PlaceOrder once per leg.  Each call must return a
    distinct non-empty broker_order_id so the orchestrator can track which leg
    filled and which to cancel.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    # Simulate two successful placement responses with distinct ids.
    handlers._client.place_order = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            ("leg_A_001", "submitted"),
            ("leg_B_002", "submitted"),
        ]
    )

    resp_a = await handlers.PlaceOrder(
        _place_req(client_order_id="cid-leg-A"),
        context=MagicMock(),
    )
    resp_b = await handlers.PlaceOrder(
        _place_req(client_order_id="cid-leg-B"),
        context=MagicMock(),
    )

    assert resp_a.broker_order_id == "leg_A_001"
    assert resp_b.broker_order_id == "leg_B_002"
    # IDs must be distinct so the orchestrator can target the correct leg.
    assert resp_a.broker_order_id != resp_b.broker_order_id
    assert resp_a.broker_order_id != ""
    assert resp_b.broker_order_id != ""


# ---------------------------------------------------------------------------
# T-O.9-2: CancelOrder works on the surviving leg
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order_works_on_survivor() -> None:
    """CancelOrder is what the orchestrator calls after one leg fills.

    When leg A fills, the orchestrator calls CancelOrder(leg_B_id).  The
    handler must return accepted=True when the SDK confirms the cancellation.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.cancel_order = AsyncMock(return_value=True)  # type: ignore[method-assign]

    resp = await handlers.CancelOrder(
        _cancel_req(broker_order_id="leg_B_002"),
        context=MagicMock(),
    )

    assert resp.accepted is True
    handlers._client.cancel_order.assert_awaited_once_with("12345678", "leg_B_002")


@pytest.mark.asyncio
async def test_cancel_order_returns_not_accepted_when_sdk_fails() -> None:
    """CancelOrder forwards SDK failure (e.g. order already filled) as accepted=False."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.cancel_order = AsyncMock(return_value=False)  # type: ignore[method-assign]

    resp = await handlers.CancelOrder(
        _cancel_req(broker_order_id="leg_B_002"),
        context=MagicMock(),
    )

    assert resp.accepted is False


# ---------------------------------------------------------------------------
# T-O.9-3: OrderEvent stream emits fill events so the orchestrator can react
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_event_stream_emits_fill_for_oco_leg() -> None:
    """OrderEvent stream must surface fills so the orchestrator can react.

    Sequence:
      1. Orchestrator subscribes to OrderEvent for account 12345678.
      2. Leg A fills (Futu fires TradeOrderHandlerBase.on_recv_rsp).
      3. The stream must yield an OrderEventMessage with status='filled'.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    request = broker_pb2.AccountRef(account_number="12345678")
    stream = handlers.OrderEvent(request, context=None)

    async def _simulate_fill() -> None:
        await asyncio.sleep(0.02)
        handlers._client._on_order_update("12345678", _fake_fill_row(1001, "cid-leg-A"))

    emit_task = asyncio.create_task(_simulate_fill())
    event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
    await emit_task

    assert event.broker_order_id == "1001"
    assert event.status == "filled"
    assert event.client_order_id == "cid-leg-A"

    await stream.aclose()


@pytest.mark.asyncio
async def test_order_event_stream_emits_cancel_for_surviving_leg() -> None:
    """After leg A fills, the orchestrator cancels leg B.  The stream should
    surface the resulting cancellation event for leg B so the orchestrator
    can close the OCO state machine.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    request = broker_pb2.AccountRef(account_number="12345678")
    stream = handlers.OrderEvent(request, context=None)

    cancel_row = {
        "order_id": 1002,
        "code": "HK.00700",
        "order_status": "CANCELLED_ALL",
        "dealt_qty": "0",
        "dealt_avg_price": "0",
        "create_time": "2026-05-06 10:00:02",
        "updated_time": "2026-05-06 10:00:03",
        "remark": "cid-leg-B",
    }

    async def _simulate_cancel() -> None:
        await asyncio.sleep(0.02)
        handlers._client._on_order_update("12345678", cancel_row)

    emit_task = asyncio.create_task(_simulate_cancel())
    event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
    await emit_task

    assert event.broker_order_id == "1002"
    assert event.status == "cancelled"
    assert event.client_order_id == "cid-leg-B"

    await stream.aclose()


# ---------------------------------------------------------------------------
# T-O.9-4: Queue drop behaviour when at capacity (Pattern D)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_event_queue_drops_when_full() -> None:
    """When the OrderEvent queue is at capacity (1000), additional events are
    dropped (not buffered) and a structlog warning is emitted.

    This prevents unbounded memory growth during slow consumers.

    Note: _dispatch_to_queues uses loop.call_soon_threadsafe when called from
    a live thread.  In the test (same loop, no worker thread), _on_order_update
    is called synchronously — _dispatch_to_queues detects no running loop at
    call time and calls _safe_put directly.  A single await asyncio.sleep(0)
    is sufficient to flush any deferred callbacks in either code path.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    # Register a bounded queue directly (mirrors what OrderEvent RPC does).
    queue: asyncio.Queue[broker_pb2.OrderEventMessage] = asyncio.Queue(maxsize=1000)
    handlers._client._order_event_queues.setdefault("12345678", []).append(queue)

    with patch("sidecar_futu.futu_client.log") as mock_log:
        # Fill to exactly capacity — all should succeed silently.
        for i in range(1000):
            handlers._client._on_order_update("12345678", _fake_fill_row(i, f"cid-{i}"))

        # Yield to the event loop so any call_soon_threadsafe callbacks are flushed.
        await asyncio.sleep(0)

        assert queue.qsize() == 1000
        mock_log.warning.assert_not_called()

        # One more event beyond capacity — must be dropped with a warning.
        handlers._client._on_order_update("12345678", _fake_fill_row(9999, "cid-overflow"))
        await asyncio.sleep(0)

        # Queue must still be at 1000 (overflow event discarded, not enqueued).
        assert queue.qsize() == 1000

        # A structlog warning must have been emitted for the overflow.
        mock_log.warning.assert_called_once_with(
            "orderevent_queue_full", account="12345678"
        )


# ---------------------------------------------------------------------------
# T-O.9-5: End-to-end OCO simulation (sim mode)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oco_sim_full_flow_leg_a_fills_then_leg_b_cancelled() -> None:
    """Full OCO sim-mode flow without hitting the real Futu gateway.

    The orchestrator must subscribe to the OrderEvent stream BEFORE placing
    orders so the queue is registered when sim.dispatch fires.  The test
    matches this ordering:

      1. Subscribe to OrderEvent (queue registered on first __anext__).
      2. Place leg A → SIM-... order_id + synthetic 'submitted' event.
      3. Place leg B → SIM-... order_id + synthetic 'submitted' event.
      4. Cancel leg B (survivor) → synthetic 'cancelled' event.
    """
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=True)

    account_number = "88888888"
    request = broker_pb2.AccountRef(account_number=account_number)
    stream = handlers.OrderEvent(request, context=None)
    ctx = MagicMock()

    # Advance the stream once so the queue is registered in _order_event_queues
    # before PlaceOrder calls sim.dispatch.  Place leg A concurrently so the
    # generator reaches its first `yield` (registering the queue) while
    # PlaceOrder is dispatching the synthetic event.
    async def _place_legs() -> tuple[str, str]:
        # Small delay so the generator has time to register its queue.
        await asyncio.sleep(0.01)
        r_a = await handlers.PlaceOrder(
            _place_req(client_order_id="oco-leg-A", account_number=account_number),
            context=ctx,
        )
        assert r_a.broker_order_id.startswith("SIM-")
        return r_a.broker_order_id, ""

    place_task = asyncio.create_task(_place_legs())
    # This __anext__() call advances the generator to the first yield, registering
    # the queue.  It then blocks until the synthetic 'submitted' event arrives.
    placed_a_event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
    leg_a_id, _ = await place_task

    assert placed_a_event.broker_order_id == leg_a_id
    assert placed_a_event.client_order_id == "oco-leg-A"

    # Place leg B — queue is now registered so dispatch works immediately.
    resp_b = await handlers.PlaceOrder(
        _place_req(
            client_order_id="oco-leg-B",
            account_number=account_number,
            side="SELL",
            limit_price="355.00",
        ),
        context=ctx,
    )
    leg_b_id = resp_b.broker_order_id
    assert leg_b_id.startswith("SIM-")
    assert leg_a_id != leg_b_id

    placed_b_event = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
    assert placed_b_event.broker_order_id == leg_b_id
    assert placed_b_event.client_order_id == "oco-leg-B"

    # Orchestrator cancels leg B (simulating leg A having been filled externally).
    cancel_resp = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number=account_number,
            broker_order_id=leg_b_id,
        ),
        context=ctx,
    )
    assert cancel_resp.accepted is True

    # Sim emits a synthetic cancel event for leg B.
    cancel_evt = await asyncio.wait_for(stream.__anext__(), timeout=2.0)
    assert cancel_evt.broker_order_id == leg_b_id
    assert cancel_evt.client_order_id == "oco-leg-B"

    await stream.aclose()
