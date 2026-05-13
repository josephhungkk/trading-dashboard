"""Phase 11a-C Task 28: /ws/ai/chat integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.ws_ai import router as ws_ai_router

pytestmark = [pytest.mark.integration, pytest.mark.no_db]


async def _jwt_subject(_: Any) -> str:
    return "ci@example.com"


@dataclass(frozen=True)
class _FakeChunk:
    text: str
    request_id: UUID


class _FakeStreamingRouter:
    def __init__(self, *, chunks: int = 3) -> None:
        self.chunks = chunks

    async def stream(self, req: Any, *, jwt_subject: str) -> Any:
        request_id = uuid4()
        for idx in range(self.chunks):
            yield _FakeChunk(text=f"chunk-{idx}", request_id=request_id)


class _ForeverRouter:
    async def stream(self, req: Any, *, jwt_subject: str) -> Any:
        await asyncio.Event().wait()
        yield _FakeChunk(text="never", request_id=uuid4())


def _make_app(router: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_ai_router)
    app.state.ai_router = router
    app.state.cors_origins = frozenset({"http://testserver"})
    return app


def _chat_frame() -> dict[str, Any]:
    return {
        "type": "chat",
        "request": {
            "messages": [{"role": "user", "content": "hi"}],
            "capability": "CODING",
            "caller": "ws_chat",
        },
    }


def test_origin_disallowed_closes_pre_accept() -> None:
    app = _make_app(_FakeStreamingRouter())
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/ws/ai/chat",
            headers={"Origin": "http://evil.com"},
        ):
            pass

    assert exc.value.code == 1008


def test_chat_frame_streams_chunks_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.ws_ai.require_admin_jwt_ws", _jwt_subject)
    app = _make_app(_FakeStreamingRouter(chunks=3))
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/ai/chat",
        headers={"Origin": "http://testserver"},
    ) as ws:
        ws.send_json(_chat_frame())
        frames = [ws.receive_json() for _ in range(4)]

    assert [frame["type"] for frame in frames] == ["chunk", "chunk", "chunk", "done"]
    assert [frame["text"] for frame in frames[:3]] == ["chunk-0", "chunk-1", "chunk-2"]
    assert frames[0]["request_id"] == frames[3]["request_id"]


def test_sixth_turn_within_window_emits_turn_rate_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.ws_ai.require_admin_jwt_ws", _jwt_subject)
    app = _make_app(_FakeStreamingRouter(chunks=0))
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/ai/chat",
        headers={"Origin": "http://testserver"},
    ) as ws:
        for _ in range(5):
            ws.send_json(_chat_frame())
            assert ws.receive_json()["type"] == "done"

        ws.send_json(_chat_frame())
        frame = ws.receive_json()

    assert frame == {
        "version": 1,
        "type": "error",
        "error_class": "TurnRateExceeded",
        "message": "max 5 turns per minute",
    }


def test_second_chat_during_active_stream_emits_active_stream_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.ws_ai.require_admin_jwt_ws", _jwt_subject)
    app = _make_app(_ForeverRouter())
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/ai/chat",
        headers={"Origin": "http://testserver"},
    ) as ws:
        ws.send_json(_chat_frame())
        ws.send_json(_chat_frame())
        frame = ws.receive_json()

    assert frame == {
        "version": 1,
        "type": "error",
        "error_class": "ActiveStreamInProgress",
        "message": "wait for the active stream to finish",
    }
