from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def test_ws_rejects_missing_auth():
    """WS connection without auth token should close with 1008."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws/bots/{uuid.uuid4()}/backtest/{uuid.uuid4()}",
        ) as ws:
            ws.receive_json()
