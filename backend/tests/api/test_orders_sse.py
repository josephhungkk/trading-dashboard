"""Tests for GET /api/orders/events SSE endpoint (Task D6)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core import metrics
from app.services.orders_sse import (
    _format_sse,
    _heartbeat_pump,
    order_events_generator,
)

# ---------------------------------------------------------------------------
# Suppress global session-scoped migration fixture (no live DB needed here).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """Override the conftest migration fixture — these tests mock all I/O."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(*, disconnected_after: int = 0) -> MagicMock:
    """Return a mock Request whose is_disconnected() returns True after N calls."""
    call_count = {"n": 0}
    req = MagicMock()

    async def _is_disconnected() -> bool:
        call_count["n"] += 1
        return call_count["n"] > disconnected_after

    req.is_disconnected = _is_disconnected
    req.headers = {}
    return req


def _make_pubsub(messages: list[dict[str, Any]]) -> MagicMock:
    """Return a mock pubsub whose listen() yields *messages* then hangs."""
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()

    async def _listen() -> AsyncGenerator[dict[str, Any]]:
        for msg in messages:
            yield msg
        # Hang forever so the generator loop controls the exit.
        while True:  # noqa: ASYNC110  # mock simulates an open stream past canned msgs
            await asyncio.sleep(10)

    pubsub.listen = _listen
    return pubsub


def _make_redis(pubsub: MagicMock) -> MagicMock:
    redis = MagicMock()
    redis.pubsub = MagicMock(return_value=pubsub)
    return redis


def _make_db(rows: list[tuple[int, str]] | None = None) -> MagicMock:
    """Return a mock AsyncSession that returns *rows* on execute."""

    class _Result:
        def __init__(self, r: list[tuple[int, str]]) -> None:
            self._rows = r

        def fetchall(self) -> list[tuple[int, str]]:
            return self._rows

    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(rows or []))
    return db


async def _collect(gen: AsyncGenerator[str]) -> list[str]:
    """Drain an async generator into a list."""
    out: list[str] = []
    async for chunk in gen:
        out.append(chunk)
    return out


# ---------------------------------------------------------------------------
# Test 1 — SSE response headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_headers() -> None:
    """GET /events must return the four mandatory SSE headers."""
    from httpx import ASGITransport, AsyncClient

    from app.core.cf_access import AdminIdentity
    from app.core.deps import get_db, require_admin_jwt
    from app.main import app

    async def _noop_auth() -> AdminIdentity:
        return AdminIdentity(email="test@example.com", kind="human", claims={})

    async def _noop_db() -> AsyncGenerator[Any]:
        yield _make_db()

    app.dependency_overrides[require_admin_jwt] = _noop_auth
    app.dependency_overrides[get_db] = _noop_db

    async def _immediate_gen(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str]:
        return
        yield  # make it a generator

    try:
        with patch("app.api.orders.order_events_generator", _immediate_gen):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/orders/events")
        assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert resp.headers["cache-control"] == "no-cache, no-transform"
        assert resp.headers["x-accel-buffering"] == "no"
        assert resp.headers["connection"] == "keep-alive"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 2 — SSE frame format
# ---------------------------------------------------------------------------


def test_sse_emits_id_event_data_format() -> None:
    """_format_sse must produce the canonical id/event/data/blank frame."""
    payload = json.dumps({"status": "filled"})
    frame = _format_sse(42, payload)
    assert frame == f"id: 42\nevent: order.update\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Test 3 — heartbeat every 10 s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_heartbeat_every_10s() -> None:
    """_heartbeat_pump must put a heartbeat comment every interval seconds."""
    real_sleep = asyncio.sleep
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)

    async def _fake_sleep(secs: float) -> None:
        # Yield once via the captured real sleep so the pump loop is
        # cooperative; without a real await the pump becomes a busy-loop
        # that starves the test's awaits.
        await real_sleep(0)

    with patch("app.services.orders_sse.asyncio.sleep", _fake_sleep):
        task = asyncio.create_task(_heartbeat_pump(queue, interval=10.0))
        # Allow at least 2 pump iterations to enqueue heartbeats.
        for _ in range(5):
            await real_sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    heartbeats: list[str] = []
    while not queue.empty():
        heartbeats.append(queue.get_nowait())

    assert len(heartbeats) >= 1, "Expected at least one heartbeat"
    assert all(h == ": heartbeat\n\n" for h in heartbeats)


# ---------------------------------------------------------------------------
# Test 4 — Last-Event-ID replay via header (P14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_resume_via_last_event_id_header() -> None:
    """Backend must replay order_events WHERE id > last_event_id from header."""
    rows = [
        (101, json.dumps({"event_id": 101, "status": "submitted"})),
        (102, json.dumps({"event_id": 102, "status": "filled"})),
    ]
    db = _make_db(rows)
    pubsub = _make_pubsub([])
    redis = _make_redis(pubsub)
    req = _make_request(disconnected_after=0)

    frames = await _collect(
        order_events_generator(req, db, redis, last_event_id=100, account_id=None)
    )

    # db.execute must have been called with last_id=100
    call_args = db.execute.call_args
    # Positional args: (stmt, params_dict)
    params = call_args[0][1] if len(call_args[0]) > 1 else {}
    assert params.get("last_id") == 100, f"Expected last_id=100 in params, got {params}"

    event_frames = [f for f in frames if f.startswith("id:")]
    assert any("101" in f for f in event_frames)
    assert any("102" in f for f in event_frames)


# ---------------------------------------------------------------------------
# Test 5 — scoped subscription: account_id only (R25)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_scoped_subscription_account_only() -> None:
    """?account_id=X must subscribe to orders:events:account:<X> only."""
    account_id = uuid4()
    target_channel = f"orders:events:account:{account_id}"

    db = _make_db()
    pubsub = _make_pubsub([])
    redis = _make_redis(pubsub)
    req = _make_request(disconnected_after=0)

    await _collect(order_events_generator(req, db, redis, last_event_id=0, account_id=account_id))

    pubsub.subscribe.assert_called_once_with(target_channel)
    pubsub.unsubscribe.assert_called_with(target_channel)


# ---------------------------------------------------------------------------
# Test 6 — fleet subscription when no account_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_fleet_subscription_when_no_account_id() -> None:
    """No account_id param -> subscribe to orders:events:fleet."""
    db = _make_db()
    pubsub = _make_pubsub([])
    redis = _make_redis(pubsub)
    req = _make_request(disconnected_after=0)

    await _collect(order_events_generator(req, db, redis, last_event_id=0, account_id=None))

    pubsub.subscribe.assert_called_once_with("orders:events:fleet")
    pubsub.unsubscribe.assert_called_with("orders:events:fleet")


# ---------------------------------------------------------------------------
# Test 7 — closes on client disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_closes_on_client_disconnect() -> None:
    """Disconnecting client must cancel the generator cleanly within 1 s."""
    db = _make_db()
    pubsub = _make_pubsub([])
    redis = _make_redis(pubsub)
    req = _make_request(disconnected_after=0)

    done = asyncio.Event()

    async def _run() -> list[str]:
        frames = await _collect(
            order_events_generator(req, db, redis, last_event_id=0, account_id=None)
        )
        done.set()
        return frames

    task = asyncio.create_task(_run())
    await asyncio.wait_for(done.wait(), timeout=1.0)
    frames = await task
    event_frames = [f for f in frames if f.startswith("id:")]
    assert event_frames == []


# ---------------------------------------------------------------------------
# Test 8 — slow client drop via per-client queue overflow (P15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_pubsub_pump_returns_on_queue_full() -> None:
    """The slow-client guard fires when ``_pubsub_pump`` cannot enqueue
    a message — `queue.put_nowait` raises QueueFull and the pump returns
    early. We exercise that contract directly with a maxsize=0 queue so
    the very first message overflows.

    Rewritten 2026-05-12 (Phase 11a-CI-debt-2): the previous test wired
    the whole generator with a maxsize=1 queue + 5 messages, but the main
    loop drained one-at-a-time fast enough that the queue never
    overflowed and the generator never reached the slow-client branch.
    Testing _pubsub_pump in isolation is the right unit of behavior.
    """
    from app.services.orders_sse import _pubsub_pump

    event_data = json.dumps({"event_id": 1, "status": "submitted"})
    pubsub = _make_pubsub([{"type": "message", "data": event_data}])
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=0)

    # maxsize=0 == unbounded. Override put_nowait to raise immediately
    # so we exercise the QueueFull → return contract.
    def _full(_item: str) -> None:
        raise asyncio.QueueFull

    queue.put_nowait = _full  # type: ignore[method-assign]

    # The pump should return cleanly (not hang) on QueueFull.
    await asyncio.wait_for(_pubsub_pump(pubsub, queue), timeout=1.0)


@pytest.mark.asyncio
async def test_sse_generator_emits_slow_client_error_when_pump_returns() -> None:
    """End-to-end: when the pump task finishes early (slow client), the
    main generator yields the slow_client error frame and increments
    sse_dropped_clients_total."""
    db = _make_db()

    pubsub = _make_pubsub([])  # no messages — pump will just hang on listen()
    redis = _make_redis(pubsub)

    req = MagicMock()
    is_disconnected_calls = [0]

    async def _is_disconnected() -> bool:
        is_disconnected_calls[0] += 1
        # Stay connected for the first tick, disconnect immediately after
        # so the main loop exits cleanly even when no messages arrive.
        # The slow-client path is exercised by manually finishing the pump
        # before the disconnect signal.
        return is_disconnected_calls[0] > 5

    req.is_disconnected = _is_disconnected

    dropped_before = metrics.sse_dropped_clients_total._value.get()

    # Patch the pump to return immediately (simulates the QueueFull early
    # return). The generator should observe pump_task.done() and set
    # slow_client = True.
    async def _pump_returns_immediately(*_a: Any, **_kw: Any) -> None:
        return None

    with patch("app.services.orders_sse._pubsub_pump", _pump_returns_immediately):
        frames = await asyncio.wait_for(
            _collect(order_events_generator(req, db, redis, last_event_id=0, account_id=None)),
            timeout=3.0,
        )

    error_frames = [f for f in frames if "slow_client" in f]
    assert error_frames, f"expected slow_client error frame, got: {frames}"

    dropped_after = metrics.sse_dropped_clients_total._value.get()
    assert dropped_after == dropped_before + 1


# ---------------------------------------------------------------------------
# Test 9 — active gauge increments/decrements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_decrements_active_gauge_on_disconnect() -> None:
    """sse_active_connections increments on connect, decrements on disconnect."""
    db = _make_db()
    pubsub = _make_pubsub([])
    redis = _make_redis(pubsub)
    req = _make_request(disconnected_after=0)

    gauge_before = metrics.sse_active_connections._value.get()

    await _collect(order_events_generator(req, db, redis, last_event_id=0, account_id=None))

    gauge_after = metrics.sse_active_connections._value.get()
    assert gauge_after == gauge_before, (
        f"Gauge net change should be 0: before={gauge_before}, after={gauge_after}"
    )
