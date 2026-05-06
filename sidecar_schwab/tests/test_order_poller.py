"""Phase 8a - adaptive order poller (2s active / 30s idle), 429 backoff, hash rotation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_schwab.order_poller import OrderPoller, compute_backoff


def _build_state_cache() -> MagicMock:
    cache = MagicMock()
    cache.hydrate = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.put = AsyncMock()
    cache.invalidate_all = AsyncMock()
    cache.known_client_order_ids = MagicMock(return_value=set())
    return cache


def _build_client() -> MagicMock:
    client = MagicMock()
    client.ensure_fresh_token = AsyncMock()
    client.get_orders_since = AsyncMock(return_value=[])
    return client


def _build_poller(*, client=None, cache=None) -> OrderPoller:
    return OrderPoller(
        client=client or _build_client(),
        state_cache=cache or _build_state_cache(),
        gateway_label="schwab-paper",
        account_id="acct1",
        account_hash_resolver=lambda: "ACCT_HASH",
    )


@pytest.mark.asyncio
async def test_diff_emits_submitted_for_new_client_order_id():
    cache = _build_state_cache()
    client = _build_client()
    client.get_orders_since = AsyncMock(
        return_value=[
            {
                "orderId": 12345,
                "clientOrderId": "cli-1",
                "status": "QUEUED",
                "enteredTime": "2026-05-06T14:30:00Z",
            }
        ]
    )
    poller = _build_poller(client=client, cache=cache)
    events = await poller._poll_once()
    assert any(e.status == "submitted" and e.kind == "status" for e in events)
    cache.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_diff_emits_status_change_for_existing_order():
    from sidecar_schwab.order_state_cache import OrderState

    cache = _build_state_cache()
    cache.get = AsyncMock(
        return_value=OrderState(
            client_order_id="cli-1",
            broker_order_id="12345",
            schwab_status="QUEUED",
        )
    )
    client = _build_client()
    client.get_orders_since = AsyncMock(
        return_value=[
            {
                "orderId": 12345,
                "clientOrderId": "cli-1",
                "status": "FILLED",
                "enteredTime": "2026-05-06T14:30:00Z",
            }
        ]
    )
    poller = _build_poller(client=client, cache=cache)
    events = await poller._poll_once()
    assert any(e.status == "filled" for e in events)


@pytest.mark.parametrize(
    "attempt,expected_delay",
    [(0, 2.0), (1, 4.0), (2, 8.0), (3, 16.0), (4, 30.0), (5, 30.0)],
)
def test_backoff_doubles_with_cap(attempt: int, expected_delay: float):
    assert compute_backoff(attempt) == expected_delay


@pytest.mark.asyncio
async def test_cadence_switches_on_in_flight():
    poller = _build_poller()
    assert poller.current_tick_seconds() == 30.0  # starts idle
    poller.activate_fast()
    assert poller.current_tick_seconds() == 2.0
    poller._mark_no_in_flight()
    assert poller.current_tick_seconds() == 30.0


@pytest.mark.asyncio
async def test_hash_rotation_invalidates_state():
    cache = _build_state_cache()
    poller = _build_poller(cache=cache)
    await poller.handle_account_hash_rotation()
    cache.invalidate_all.assert_awaited_once()
    assert poller._in_flight == set()


@pytest.mark.asyncio
async def test_fanout_subscribe_unsubscribe_roundtrip():
    poller = _build_poller()
    fan = poller.fan_out()
    q = fan.subscribe()
    fan.unsubscribe(q)
    # idempotent
    fan.unsubscribe(q)


@pytest.mark.asyncio
async def test_fanout_drops_slow_consumer_on_queue_full():
    from sidecar_schwab.order_poller import WireEvent
    import asyncio as _asyncio

    poller = _build_poller()
    fan = poller.fan_out()
    fan.subscribe()
    tiny: _asyncio.Queue = _asyncio.Queue(maxsize=1)
    fan._subs[0] = tiny
    ev = WireEvent(
        broker_order_id="1", client_order_id="cli-1", kind="status", status="submitted"
    )
    await fan.publish(ev)  # fills the queue
    await fan.publish(ev)  # would overflow -> drops subscriber
    assert tiny not in fan._subs
