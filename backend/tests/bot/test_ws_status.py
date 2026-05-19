from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_ws_bots_status_connects():
    """WS /ws/bots/status accepts connection."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/bots/status") as ws:
            ws.close()


def test_ws_cap_50():
    """WS cap enforced at 50 connections."""
    from app.api.ws_bots import _active

    original = set(_active)
    _active.clear()
    for _ in range(50):
        _active.add(object())  # type: ignore[arg-type]

    try:
        with TestClient(app) as client:
            with pytest.raises((Exception, RuntimeError)):
                client.websocket_connect("/ws/bots/status").__enter__()
    finally:
        _active.clear()
        _active.update(original)
