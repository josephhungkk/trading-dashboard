from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ws_bots import _active, router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    # Provide a minimal Redis mock that satisfies pubsub usage.
    pubsub_mock = MagicMock()
    pubsub_mock.psubscribe = AsyncMock()
    pubsub_mock.punsubscribe = AsyncMock()
    pubsub_mock.aclose = AsyncMock()

    async def _listen():
        # Yield one non-message frame then stop (simulates idle pubsub).
        yield {"type": "subscribe", "data": 1}

    pubsub_mock.listen = _listen

    redis_mock = MagicMock()
    redis_mock.pubsub = MagicMock(return_value=pubsub_mock)

    app.state.redis = redis_mock
    return app


def test_ws_bots_status_connects():
    """WS /ws/bots/status accepts connection."""
    app = _make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/bots/status") as ws:
            ws.close()


def test_ws_cap_50():
    """WS cap enforced at 50 connections."""
    original = set(_active)
    _active.clear()
    for _ in range(50):
        _active.add(object())  # type: ignore[arg-type]

    app = _make_app()
    try:
        with TestClient(app) as client:
            with pytest.raises((Exception, RuntimeError)):
                client.websocket_connect("/ws/bots/status").__enter__()
    finally:
        _active.clear()
        _active.update(original)
