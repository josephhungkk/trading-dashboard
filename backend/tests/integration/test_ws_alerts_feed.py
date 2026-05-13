"""Phase 11b-C4: /ws/alerts/feed integration tests.

Mirrors the test_ws_ai_jobs.py pattern: handshake/origin gate via ASGI
scope, pubsub fan-out via fakeredis, frame schema (v=1, type=fire).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI

import app.api.ws_alerts as _ws_alerts
from app.api.ws_alerts import router as ws_alerts_router

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

Message = MutableMapping[str, Any]
SendHook = Callable[[Message], Awaitable[None]]


@pytest.fixture(autouse=True)
def _reset_ws_counters() -> Iterator[None]:
    _ws_alerts._active_feed_connections = 0
    yield
    _ws_alerts._active_feed_connections = 0


def _make_app(fake_redis: fakeredis.aioredis.FakeRedis) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_alerts_router)
    app.state.redis = fake_redis
    app.state.cors_origins = frozenset({"http://testserver"})
    return app


def _make_scope(origin: bytes = b"http://testserver") -> dict[str, Any]:
    path = "/ws/alerts/feed"
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"origin", origin)],
        "client": ("10.10.0.1", 50001),
        "server": ("testserver", 80),
        "subprotocols": [],
        "root_path": "",
    }


async def _run_ws(
    app: FastAPI,
    *,
    scope_overrides: dict[str, Any] | None = None,
    on_send: SendHook | None = None,
    extra_receives: list[Message] | None = None,
    timeout_s: float = 3.0,
) -> list[Message]:
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    for m in extra_receives or []:
        await queue.put(m)

    scope = _make_scope()
    if scope_overrides:
        scope.update(scope_overrides)

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if on_send is not None:
            await on_send(message)

    with pytest.raises((asyncio.TimeoutError, Exception)):
        await asyncio.wait_for(app(scope, receive, send), timeout=timeout_s)
    return messages


async def test_origin_mismatch_closes_with_1008() -> None:
    """Off-allowlist Origin header is rejected pre-accept with WS_1008_POLICY_VIOLATION."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        app = _make_app(fake_redis)
        msgs: list[Message] = []
        queue: asyncio.Queue[Message] = asyncio.Queue()
        await queue.put({"type": "websocket.connect"})

        scope = _make_scope(origin=b"https://evil.example.com")
        # Client IP must NOT be 10.10.0.1 (that's the WG dev bypass).
        scope["client"] = ("203.0.113.5", 50001)

        async def receive() -> Message:
            return await queue.get()

        async def send(message: Message) -> None:
            msgs.append(message)

        await asyncio.wait_for(app(scope, receive, send), timeout=3.0)

        close = next(m for m in msgs if m.get("type") == "websocket.close")
        assert close["code"] == 1008
        assert close.get("reason") == "origin"
    finally:
        await fake_redis.aclose()


async def test_pubsub_fire_emits_v1_fire_frame() -> None:
    """A message published to alerts:fire:{subject} is forwarded as
    {version:1, type:"fire", ...payload}."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        app = _make_app(fake_redis)
        msgs: list[Message] = []
        queue: asyncio.Queue[Message] = asyncio.Queue()
        await queue.put({"type": "websocket.connect"})

        scope = _make_scope()
        scope["client"] = ("10.10.0.1", 50001)  # WG dev bypass — jwt_subject="dev-bypass"

        accepted = asyncio.Event()
        nonlocal_state: dict[str, Any] = {"published": False}

        async def receive() -> Message:
            return await queue.get()

        async def send(message: Message) -> None:
            msgs.append(message)
            if message.get("type") == "websocket.accept":
                accepted.set()

        async def driver() -> None:
            await accepted.wait()
            # Tiny delay so the endpoint enters pubsub.listen() before we publish.
            await asyncio.sleep(0.05)
            payload = json.dumps(
                {
                    "fire_id": 7,
                    "alert_id": 42,
                    "user_label": "AAPL above 200",
                    "verdict": "true",
                    "evaluated_values": {"close": 201.5},
                }
            )
            await fake_redis.publish("alerts:fire:dev-bypass", payload)
            nonlocal_state["published"] = True
            # Wait for the frame to be forwarded then disconnect.
            await asyncio.sleep(0.1)
            await queue.put({"type": "websocket.disconnect", "code": 1000})

        run = asyncio.create_task(app(scope, receive, send))
        drive = asyncio.create_task(driver())
        with __import__("contextlib").suppress(Exception):
            await asyncio.wait_for(run, timeout=3.0)
        drive.cancel()

        assert nonlocal_state["published"], "test driver never published"
        fire_frames = [
            json.loads(m["text"])
            for m in msgs
            if m.get("type") == "websocket.send" and "text" in m and m["text"]
        ]
        assert any(
            f.get("v") == 1
            and f.get("type") == "fire"
            and f.get("fire_id") == 7
            and f.get("alert_id") == 42
            for f in fire_frames
        ), f"no v=1 fire frame seen; frames={fire_frames}"
    finally:
        await fake_redis.aclose()


async def test_disconnect_releases_subscription_and_counter() -> None:
    """Codex chunk-C test-gap MED — client disconnect must terminate the
    handler task and decrement the connection counter even when no pubsub
    message arrives, otherwise pubsub.listen() leaks the slot."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        app = _make_app(fake_redis)
        msgs: list[Message] = []
        queue: asyncio.Queue[Message] = asyncio.Queue()
        await queue.put({"type": "websocket.connect"})

        scope = _make_scope()
        scope["client"] = ("10.10.0.1", 50001)

        accepted = asyncio.Event()

        async def receive() -> Message:
            return await queue.get()

        async def send(message: Message) -> None:
            msgs.append(message)
            if message.get("type") == "websocket.accept":
                accepted.set()

        async def driver() -> None:
            await accepted.wait()
            # Disconnect immediately without ever publishing.
            await asyncio.sleep(0.05)
            await queue.put({"type": "websocket.disconnect", "code": 1000})

        run = asyncio.create_task(app(scope, receive, send))
        drive = asyncio.create_task(driver())
        # Bounded wait: if the handler leaks pubsub.listen() this will time
        # out and the assertion below catches it explicitly.
        await asyncio.wait_for(asyncio.gather(run, drive, return_exceptions=True), timeout=3.0)
        assert _ws_alerts._active_feed_connections == 0
    finally:
        await fake_redis.aclose()
