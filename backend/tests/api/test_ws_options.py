"""Tests for WS /ws/options/chain."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.ws_options import router

pytestmark = [pytest.mark.no_db]


def _make_app(chain_svc: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Provide a stub redis so get_chain_service doesn't fail on state access
    app.state.redis = AsyncMock()
    if chain_svc is not None:
        app.state._chain_svc_override = chain_svc
    return app


def test_ws_options_connect_and_disconnect() -> None:
    """Client should be able to connect and receive an initial frame."""
    svc = MagicMock()
    svc.get_chain = AsyncMock(
        return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0, "stale": False}
    )

    with patch("app.api.ws_options.get_chain_service", return_value=svc):
        app = _make_app()
        client = TestClient(app)
        with client.websocket_connect("/ws/options/chain?symbol=SPY&expiry=2025-01-17") as ws:
            data = ws.receive_json(mode="text")
            assert data.get("type") in ("chain", "stale", "heartbeat")


def test_ws_options_connection_cap() -> None:
    """Connections beyond _CONNECTION_CAP should be rejected."""
    import app.api.ws_options as ws_mod

    original = ws_mod._active_connections
    ws_mod._active_connections = ws_mod._CONNECTION_CAP
    try:
        app_inst = FastAPI()
        app_inst.include_router(router)
        app_inst.state.redis = AsyncMock()
        client = TestClient(app_inst)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/options/chain?symbol=SPY&expiry=2025-01-17"):
                pass
    finally:
        ws_mod._active_connections = original
