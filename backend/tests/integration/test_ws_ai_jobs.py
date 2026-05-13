"""Phase 11a-C Task 29: /ws/ai/jobs/{id} integration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from fastapi import FastAPI

from app.api.ws_ai import router as ws_ai_router
from app.services.ai.jobs import JobRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

Message = MutableMapping[str, Any]
SendHook = Callable[[Message], Awaitable[None]]


@dataclass
class _FakeJobRouter:
    jobs: dict[UUID, JobRecord]

    async def get_job(self, job_id: UUID) -> JobRecord | None:
        return self.jobs.get(job_id)


def _make_app(
    fake_redis: fakeredis.aioredis.FakeRedis,
    router: _FakeJobRouter,
) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_ai_router)
    app.state.redis = fake_redis
    app.state.ai_router = router
    app.state.cors_origins = frozenset({"http://testserver"})
    return app


def _make_scope(job_id: UUID) -> dict[str, Any]:
    path = f"/ws/ai/jobs/{job_id}"
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"origin", b"http://testserver")],
        "client": ("10.10.0.1", 50001),
        "server": ("testserver", 80),
        "subprotocols": [],
        "root_path": "",
    }


async def _run_ws(
    app: FastAPI,
    job_id: UUID,
    *,
    on_send: SendHook | None = None,
) -> list[Message]:
    messages: list[Message] = []
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put({"type": "websocket.connect"})
    scope = _make_scope(job_id)

    async def receive() -> Message:
        return await queue.get()

    async def send(message: Message) -> None:
        messages.append(message)
        if on_send is not None:
            await on_send(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=3.0)
    return messages


def _job_record(
    *,
    job_id: UUID,
    jwt_subject: str,
    status: str = "pending",
) -> JobRecord:
    started_at = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return JobRecord(
        id=job_id,
        jwt_subject=jwt_subject,
        status=status,
        capability="CODING",
        request_jsonb={"messages": [{"role": "user", "content": "hi"}]},
        response_jsonb=None,
        error=None,
        started_at=started_at,
        warming_started_at=None,
        inferring_started_at=None,
        completed_at=None,
        cancel_requested=False,
    )


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


async def test_unknown_job_closes_with_1008_job_not_found(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    job_id = uuid4()
    app = _make_app(fake_redis, _FakeJobRouter(jobs={}))

    messages = await _run_ws(app, job_id)

    close = next(message for message in messages if message["type"] == "websocket.close")
    assert close["code"] == 1008
    assert close["reason"] == "job_not_found"


async def test_other_subject_closes_with_1008_job_not_found(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    job_id = uuid4()
    app = _make_app(
        fake_redis,
        _FakeJobRouter(jobs={job_id: _job_record(job_id=job_id, jwt_subject="other@example.com")}),
    )

    messages = await _run_ws(app, job_id)

    close = next(message for message in messages if message["type"] == "websocket.close")
    assert close["code"] == 1008
    assert close["reason"] == "job_not_found"


async def test_initial_state_frame_sent_after_accept(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    job_id = uuid4()
    app = _make_app(
        fake_redis,
        _FakeJobRouter(jobs={job_id: _job_record(job_id=job_id, jwt_subject="dev-bypass")}),
    )
    channel = f"ai:job:{job_id}"
    published = False

    async def on_send(message: Message) -> None:
        nonlocal published
        if message["type"] == "websocket.send" and not published:
            published = True
            await fake_redis.publish(channel, json.dumps({"state": "completed"}))

    messages = await _run_ws(app, job_id, on_send=on_send)

    state_frames = [
        json.loads(message["text"])
        for message in messages
        if message["type"] == "websocket.send" and "text" in message
    ]
    assert state_frames[0] == {
        "version": 1,
        "type": "state",
        "state": "pending",
        "job_id": str(job_id),
    }


async def test_publish_terminal_state_closes_after_emit(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    job_id = uuid4()
    app = _make_app(
        fake_redis,
        _FakeJobRouter(jobs={job_id: _job_record(job_id=job_id, jwt_subject="dev-bypass")}),
    )
    channel = f"ai:job:{job_id}"
    published = False

    async def on_send(message: Message) -> None:
        nonlocal published
        if message["type"] == "websocket.send" and not published:
            published = True
            await fake_redis.publish(
                channel,
                json.dumps({"state": "completed", "response": {"text": "done"}}),
            )

    messages = await _run_ws(app, job_id, on_send=on_send)

    state_frames = [
        json.loads(message["text"])
        for message in messages
        if message["type"] == "websocket.send" and "text" in message
    ]
    assert state_frames[-1] == {
        "version": 1,
        "type": "state",
        "state": "completed",
        "job_id": str(job_id),
        "response": {"text": "done"},
    }
    close = messages[-1]
    assert close["type"] == "websocket.close"
    assert close["code"] == 1000
