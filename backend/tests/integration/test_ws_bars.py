"""Task 31 — /ws/bars/<canonical_id>/<timeframe> WebSocket gateway tests.

All 10 tests use the raw ASGI transport pattern (scope/receive/send queues)
to avoid httpx WS limitations.  fakeredis.aioredis is used as the Redis
pubsub backend.  BarService is NOT exercised — only the WS gateway in
bars.py (ws_router) and its direct dependencies.

Marks: asyncio (auto via pytest.ini asyncio_mode=auto), integration, no_db.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from typing import Any
from unittest.mock import MagicMock

import fakeredis.aioredis
import pytest
from fastapi import FastAPI

from app.api.bars import _FINAL_REVISION, _WS_DEFAULT_MAX_SUBS, ws_router
from app.core import deps as _deps_mod

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

Message = MutableMapping[str, Any]

_CANONICAL = "AAPL.US"
_TF = "1m"
_WS_PATH = f"/ws/bars/{_CANONICAL}/{_TF}"
_VALID_TOKEN = "valid-jwt-token"
_VALID_SUBPROTOCOL = f"bearer.{_VALID_TOKEN}"

# ---------------------------------------------------------------------------
# Helpers — app factory
# ---------------------------------------------------------------------------


def _valid_identity() -> Any:
    return MagicMock(email="admin@test.local", kind="cf_access_jwt")


def _make_app(fake_redis: Any | None = None) -> FastAPI:
    """Minimal FastAPI app wired with ws_router and optional fakeredis."""
    app = FastAPI()
    app.include_router(ws_router)
    if fake_redis is not None:
        app.state.redis = fake_redis
    return app


def _make_headers(
    subprotocols: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> list[tuple[bytes, bytes]]:
    raw: list[tuple[bytes, bytes]] = []
    if subprotocols:
        raw.append((b"sec-websocket-protocol", ", ".join(subprotocols).encode()))
    for k, v in (extra or {}).items():
        raw.append((k.lower().encode(), v.encode()))
    return raw


def _make_scope(path: str, subprotocols: list[str] | None) -> dict[str, Any]:
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": _make_headers(subprotocols),
        "client": ("testclient", 50001),
        "server": ("testserver", 80),
        "subprotocols": subprotocols or [],
        "root_path": "",
    }


def _make_bar_payload(
    canonical_id: str,
    tf: str,
    *,
    revision: int,
    partial: bool,
    bucket_start: str = "2026-05-01T10:00:00+00:00",
) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "tf": tf,
        "bucket_start": bucket_start,
        "open": "180.12",
        "high": "181.00",
        "low": "179.00",
        "close": "180.50",
        "volume": "1000.0",
        "trade_count": 42,
        "revision": revision,
        "partial": partial,
    }


async def _publish_after(
    redis: Any,
    channel: str,
    payload: bytes,
    delay: float = 0.15,
) -> None:
    await asyncio.sleep(delay)
    await redis.publish(channel, payload)


async def _publish_sequence(
    redis: Any,
    channel: str,
    payloads: list[bytes],
    delay_between: float = 0.1,
) -> None:
    await asyncio.sleep(0.15)
    for payload in payloads:
        await redis.publish(channel, payload)
        await asyncio.sleep(delay_between)


async def _disconnect_after(queue: asyncio.Queue[Message], delay: float = 1.0) -> None:
    await asyncio.sleep(delay)
    await queue.put({"type": "websocket.disconnect", "code": 1000})


async def _fake_get_ws_max_subs() -> int:
    """Return default 20 without DB access — used in tests."""
    return _WS_DEFAULT_MAX_SUBS


# ---------------------------------------------------------------------------
# Test 1 — handshake accepted with valid bearer subprotocol
# ---------------------------------------------------------------------------


async def test_handshake_via_subprotocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect with bearer.<valid_jwt>; assert accepted + subprotocol echoed."""
    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    accept = next((m for m in messages if m["type"] == "websocket.accept"), None)
    assert accept is not None, f"No accept message; got: {[m['type'] for m in messages]}"
    assert accept["subprotocol"] == _VALID_SUBPROTOCOL


# ---------------------------------------------------------------------------
# Test 2 — missing subprotocol → close 4001
# ---------------------------------------------------------------------------


async def test_handshake_rejects_missing_token() -> None:
    """Connect with no subprotocol; assert close-frame 4001 unauthenticated."""
    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, None)

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None, f"Expected close, got: {[m['type'] for m in messages]}"
    assert close["code"] == 4001


# ---------------------------------------------------------------------------
# Test 3 — bad token → close 4001
# ---------------------------------------------------------------------------


async def test_handshake_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect with bearer.bogus; assert close-frame 4001."""
    from jwt.exceptions import PyJWTError

    monkeypatch.setattr(
        _deps_mod._verifier, "verify", MagicMock(side_effect=PyJWTError("bad token"))
    )
    monkeypatch.setattr(_deps_mod._verifier, "check_dev_bypass", MagicMock(return_value=None))
    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, ["bearer.bogus"])

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None, f"Expected close, got: {[m['type'] for m in messages]}"
    assert close["code"] == 4001


# ---------------------------------------------------------------------------
# Test 4 — idle PING after 60s (mocked to 50ms)
# ---------------------------------------------------------------------------


async def test_idle_ping_after_60s(monkeypatch: pytest.MonkeyPatch) -> None:
    """After ping interval idle, server sends {op: ping, ts: ...}."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 0.05)
    monkeypatch.setattr(bars_module, "_WS_PONG_TIMEOUT", 0.05)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    ping_received = asyncio.Event()

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            pass  # let the ping loop fire
        elif message["type"] == "websocket.send":
            data = json.loads(message["text"])
            if data.get("op") == "ping":
                ping_received.set()
                await queue.put({"type": "websocket.disconnect", "code": 1000})
        elif message["type"] == "websocket.close":
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(ping_received.wait(), timeout=3.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    ping_msgs = [
        json.loads(m["text"])
        for m in messages
        if m["type"] == "websocket.send" and json.loads(m["text"]).get("op") == "ping"
    ]
    assert len(ping_msgs) >= 1, f"No ping received; messages={messages}"
    assert "ts" in ping_msgs[0]


# ---------------------------------------------------------------------------
# Test 5 — idle close after no pong
# ---------------------------------------------------------------------------


async def test_idle_close_after_no_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    """No pong after ping → server-close 1000 idle_timeout."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 0.05)
    monkeypatch.setattr(bars_module, "_WS_PONG_TIMEOUT", 0.05)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.close":
            # Unblock the handler
            await queue.put({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(app(scope, receive, send), timeout=5.0)

    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None, f"No close message; got: {[m['type'] for m in messages]}"
    assert close["code"] == 1000
    assert close.get("reason", "") == "idle_timeout"


# ---------------------------------------------------------------------------
# Test 6 — subscribe + receive bar messages
# ---------------------------------------------------------------------------


async def test_subscribe_receives_bar_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subscribe to (AAPL.US, 1m); publish a bar to Redis; assert client receives it."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 999.0)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app = _make_app(fake_redis)

    subscribe_msg = json.dumps({"op": "subscribe", "canonical_id": _CANONICAL, "timeframe": _TF})
    bar_payload = _make_bar_payload(_CANONICAL, _TF, revision=1, partial=True)
    bar_bytes = json.dumps(bar_payload).encode()

    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    got_bar = asyncio.Event()

    async def receive() -> Message:
        return await queue.get()

    bg_tasks: set[asyncio.Task[None]] = set()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            await queue.put({"type": "websocket.receive", "text": subscribe_msg, "bytes": None})
            t = asyncio.create_task(
                _publish_after(fake_redis, f"bar.{_CANONICAL}.{_TF}", bar_bytes)
            )
            bg_tasks.add(t)
            t.add_done_callback(bg_tasks.discard)
        elif message["type"] == "websocket.send":
            data = json.loads(message["text"])
            if data.get("canonical_id") == _CANONICAL:
                got_bar.set()
                await queue.put({"type": "websocket.disconnect", "code": 1000})

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(got_bar.wait(), timeout=4.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    bar_msgs = [
        json.loads(m["text"])
        for m in messages
        if m["type"] == "websocket.send" and json.loads(m["text"]).get("canonical_id") == _CANONICAL
    ]
    assert len(bar_msgs) >= 1
    first = bar_msgs[0]
    assert first["canonical_id"] == _CANONICAL
    assert first["timeframe"] == _TF


# ---------------------------------------------------------------------------
# Test 7 — revision sequenced delivery
# ---------------------------------------------------------------------------


async def test_revision_sequenced_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish 3 bars with revisions 1,2,3; assert client received in monotonic order."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 999.0)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app = _make_app(fake_redis)

    subscribe_msg = json.dumps({"op": "subscribe", "canonical_id": _CANONICAL, "timeframe": _TF})
    channel = f"bar.{_CANONICAL}.{_TF}"
    payloads = [
        json.dumps(_make_bar_payload(_CANONICAL, _TF, revision=r, partial=True)).encode()
        for r in [1, 2, 3]
    ]

    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    received_revisions: list[int] = []
    done_event = asyncio.Event()
    bg_tasks7: set[asyncio.Task[None]] = set()

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            await queue.put({"type": "websocket.receive", "text": subscribe_msg, "bytes": None})
            t = asyncio.create_task(_publish_sequence(fake_redis, channel, payloads))
            bg_tasks7.add(t)
            t.add_done_callback(bg_tasks7.discard)
        elif message["type"] == "websocket.send":
            data = json.loads(message["text"])
            if data.get("canonical_id") == _CANONICAL:
                received_revisions.append(data["revision"])
                if len(received_revisions) >= 3:
                    done_event.set()
                    await queue.put({"type": "websocket.disconnect", "code": 1000})

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(done_event.wait(), timeout=5.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert received_revisions == sorted(received_revisions), (
        f"Revisions not monotonic: {received_revisions}"
    )


# ---------------------------------------------------------------------------
# Test 8 — partial=false carries max revision (2**31-1)
# ---------------------------------------------------------------------------


async def test_partial_false_carries_max_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish bar with partial=False; assert envelope.revision == 2**31-1."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 999.0)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app = _make_app(fake_redis)

    subscribe_msg = json.dumps({"op": "subscribe", "canonical_id": _CANONICAL, "timeframe": _TF})
    channel = f"bar.{_CANONICAL}.{_TF}"
    # partial=False bar — revision in payload doesn't matter, ws_bars should override
    bar_payload = _make_bar_payload(_CANONICAL, _TF, revision=1, partial=False)
    bar_bytes = json.dumps(bar_payload).encode()

    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    received: list[dict[str, Any]] = []
    done_event = asyncio.Event()
    bg_tasks8: set[asyncio.Task[None]] = set()

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        if message["type"] == "websocket.accept":
            await queue.put({"type": "websocket.receive", "text": subscribe_msg, "bytes": None})
            t = asyncio.create_task(_publish_after(fake_redis, channel, bar_bytes))
            bg_tasks8.add(t)
            t.add_done_callback(bg_tasks8.discard)
        elif message["type"] == "websocket.send":
            data = json.loads(message["text"])
            if data.get("canonical_id") == _CANONICAL:
                received.append(data)
                done_event.set()
                await queue.put({"type": "websocket.disconnect", "code": 1000})

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(done_event.wait(), timeout=4.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(received) >= 1
    assert received[0]["revision"] == _FINAL_REVISION == 2**31 - 1


# ---------------------------------------------------------------------------
# Test 9 — subscription limit exceeded on 21st subscribe
# ---------------------------------------------------------------------------


async def test_subscription_limit_exceeded_on_21st(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subscribe to 20 distinct (canonical, tf); 21st triggers close 4029."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 999.0)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    app = _make_app()
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    # 21 distinct (canonical, tf) subscribe messages
    sub_msgs: list[str] = [
        json.dumps({"op": "subscribe", "canonical_id": f"SYM{i}.US", "timeframe": "1m"})
        for i in range(21)
    ]

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            for msg in sub_msgs:
                await queue.put({"type": "websocket.receive", "text": msg, "bytes": None})
        elif message["type"] == "websocket.close":
            await queue.put({"type": "websocket.disconnect", "code": message.get("code", 1000)})

    await asyncio.wait_for(app(scope, receive, send), timeout=5.0)

    close = next((m for m in messages if m["type"] == "websocket.close"), None)
    assert close is not None, f"Expected close; got: {[m['type'] for m in messages]}"
    assert close["code"] == 4029
    assert close.get("reason", "") == "subscription_limit_exceeded"


# ---------------------------------------------------------------------------
# Test 10 — unsubscribe stops delivery
# ---------------------------------------------------------------------------


async def test_unsubscribe_stops_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subscribe then unsubscribe; publish bar; assert NOT received."""
    import app.api.bars as bars_module

    monkeypatch.setattr(_deps_mod._verifier, "verify", MagicMock(return_value=_valid_identity()))
    monkeypatch.setattr(bars_module, "_WS_PING_INTERVAL", 999.0)
    monkeypatch.setattr(bars_module, "_get_ws_max_subs", _fake_get_ws_max_subs)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app = _make_app(fake_redis)

    subscribe_msg = json.dumps({"op": "subscribe", "canonical_id": _CANONICAL, "timeframe": _TF})
    unsub_msg = json.dumps({"op": "unsubscribe", "canonical_id": _CANONICAL, "timeframe": _TF})
    channel = f"bar.{_CANONICAL}.{_TF}"
    bar_bytes = json.dumps(_make_bar_payload(_CANONICAL, _TF, revision=1, partial=True)).encode()

    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(_WS_PATH, [_VALID_SUBPROTOCOL])

    async def receive() -> Message:
        return await queue.get()

    bg_tasks10: set[asyncio.Task[None]] = set()

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "websocket.accept":
            # subscribe then immediately unsubscribe
            await queue.put({"type": "websocket.receive", "text": subscribe_msg, "bytes": None})
            await queue.put({"type": "websocket.receive", "text": unsub_msg, "bytes": None})
            # Publish after unsubscribe has been processed; then disconnect
            t1 = asyncio.create_task(_publish_after(fake_redis, channel, bar_bytes, delay=0.3))
            bg_tasks10.add(t1)
            t1.add_done_callback(bg_tasks10.discard)
            t2 = asyncio.create_task(_disconnect_after(queue, delay=0.8))
            bg_tasks10.add(t2)
            t2.add_done_callback(bg_tasks10.discard)

    await asyncio.wait_for(app(scope, receive, send), timeout=5.0)

    bar_msgs = [
        m
        for m in messages
        if m["type"] == "websocket.send" and json.loads(m["text"]).get("canonical_id") == _CANONICAL
    ]
    assert len(bar_msgs) == 0, f"Bar received after unsubscribe: {bar_msgs}"
