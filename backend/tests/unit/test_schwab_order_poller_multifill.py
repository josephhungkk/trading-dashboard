"""CRIT-3: multi-fill dedup uses last_exec_ids set, not single last_exec_id.

Simulates 3 partial fills arriving in a single poll batch.  On the next poll
(same fills returned again), none should be re-emitted as duplicate SSE events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Phase 9.7: misplaced test (sidecar_schwab module). Skip if the schwab
# proto stubs aren't on sys.path — the backend CI job doesn't generate
# them. Schwab CI job still exercises the test.
pytest.importorskip("sidecar_schwab._generated.broker.v1.broker_pb2")
from sidecar_schwab.order_poller import OrderPoller
from sidecar_schwab.order_state_cache import OrderState, OrderStateCache


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    async def hget(self, name: str, key: str) -> str | None:
        return self._store.get(name, {}).get(key)

    async def hset(self, name: str, key: str, value: str) -> None:
        self._store.setdefault(name, {})[key] = value

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._store.get(name, {}))

    async def expire(self, name: str, seconds: int) -> None:
        pass

    async def delete(self, name: str) -> None:
        self._store.pop(name, None)


def _make_schwab_order(
    order_id: str,
    client_order_id: str,
    status: str,
    fill_leg_ids: list[str],
) -> dict:
    """Build a minimal Schwab REST order dict with fill legs."""
    legs = [
        {
            "legId": lid,
            "price": "150.00",
            "quantity": "10",
            "time": "2026-05-08T10:00:00+00:00",
        }
        for lid in fill_leg_ids
    ]
    activities = [{"executionType": "FILL", "executionLegs": legs}] if legs else []
    return {
        "orderId": order_id,
        "clientOrderId": client_order_id,
        "status": status,
        "quantity": "30",
        "marketValue": "4500.00",
        "orderActivityCollection": activities,
        "enteredTime": "2026-05-08T09:55:00+00:00",
    }


@pytest.mark.asyncio
async def test_three_fills_in_one_batch_all_emitted_once() -> None:
    """CRIT-3: 3 fills in one poll → 3 fill events; 0 on second poll of same data."""
    redis = _FakeRedis()
    state_cache = OrderStateCache(redis=redis, gateway_label="schwab", account_id="acct-1")
    await state_cache.hydrate()

    fake_client = MagicMock()
    fake_client.ensure_fresh_token = AsyncMock()

    coid = "client-order-abc"
    order_id = "12345"
    fill_ids = ["fill-1", "fill-2", "fill-3"]
    row = _make_schwab_order(order_id, coid, "FILLED", fill_ids)
    fake_client.get_orders_since = AsyncMock(return_value=[row])

    poller = OrderPoller(
        client=fake_client,
        state_cache=state_cache,
        gateway_label="schwab",
        account_id="acct-1",
        account_hash_resolver=lambda: "HASH",
    )
    poller._last_poll_iso = "2026-01-01T00:00:00+00:00"

    # First poll — all 3 fills should be emitted.
    events_1 = await poller._poll_once()
    fill_events_1 = [e for e in events_1 if e.kind == "fill"]
    assert len(fill_events_1) == 3, f"Expected 3 fills, got: {fill_events_1}"
    assert {e.exec_id for e in fill_events_1} == set(fill_ids)

    # Second poll — same fills in response, none should be re-emitted.
    events_2 = await poller._poll_once()
    fill_events_2 = [e for e in events_2 if e.kind == "fill"]
    assert fill_events_2 == [], f"Duplicate fill events on second poll: {fill_events_2}"


@pytest.mark.asyncio
async def test_new_fill_on_second_poll_emitted() -> None:
    """CRIT-3: a genuinely new fill on the second poll must still be emitted."""
    redis = _FakeRedis()
    state_cache = OrderStateCache(redis=redis, gateway_label="schwab", account_id="acct-2")
    await state_cache.hydrate()

    fake_client = MagicMock()
    fake_client.ensure_fresh_token = AsyncMock()

    coid = "client-order-xyz"
    order_id = "99999"

    fake_client.get_orders_since = AsyncMock(
        return_value=[_make_schwab_order(order_id, coid, "WORKING", ["fill-A"])]
    )

    poller = OrderPoller(
        client=fake_client,
        state_cache=state_cache,
        gateway_label="schwab",
        account_id="acct-2",
        account_hash_resolver=lambda: "HASH",
    )
    poller._last_poll_iso = "2026-01-01T00:00:00+00:00"

    events_1 = await poller._poll_once()
    fill_events_1 = [e for e in events_1 if e.kind == "fill"]
    assert len(fill_events_1) == 1
    assert fill_events_1[0].exec_id == "fill-A"

    # Second poll: fill-A (old) + fill-B (new).
    fake_client.get_orders_since = AsyncMock(
        return_value=[_make_schwab_order(order_id, coid, "FILLED", ["fill-A", "fill-B"])]
    )

    events_2 = await poller._poll_once()
    fill_events_2 = [e for e in events_2 if e.kind == "fill"]
    assert len(fill_events_2) == 1, f"Expected only fill-B, got: {fill_events_2}"
    assert fill_events_2[0].exec_id == "fill-B"


@pytest.mark.asyncio
async def test_state_cache_serialises_exec_id_set() -> None:
    """CRIT-3: last_exec_ids round-trips through Redis JSON serialization correctly."""
    redis = _FakeRedis()
    cache = OrderStateCache(redis=redis, gateway_label="schwab", account_id="acct-3")

    state = OrderState(
        client_order_id="co-1",
        broker_order_id="bo-1",
        schwab_status="FILLED",
        last_exec_ids={"fill-X", "fill-Y", "fill-Z"},
    )
    await cache.put(state)

    # Clear in-memory layer to force re-read from Redis.
    cache._mem.clear()

    loaded = await cache.get("co-1")
    assert loaded is not None
    assert loaded.last_exec_ids == {"fill-X", "fill-Y", "fill-Z"}


@pytest.mark.asyncio
async def test_state_cache_migrates_old_last_exec_id_format() -> None:
    """CRIT-3: old Redis records with last_exec_id (str) are migrated to set on read."""
    import json

    redis = _FakeRedis()
    cache = OrderStateCache(redis=redis, gateway_label="schwab", account_id="acct-4")

    # Inject an old-format record directly into Redis.
    old_record = json.dumps(
        {
            "client_order_id": "co-old",
            "broker_order_id": "bo-old",
            "schwab_status": "FILLED",
            "entered_time_iso": "",
            "last_exec_id": "fill-legacy",  # old single-string field
        }
    )
    await redis.hset(cache._key, "co-old", old_record)

    loaded = await cache.get("co-old")
    assert loaded is not None
    assert "fill-legacy" in loaded.last_exec_ids
