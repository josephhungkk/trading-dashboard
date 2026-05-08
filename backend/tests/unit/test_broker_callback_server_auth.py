"""Unit test: HIGH-sec-3 — BackendCallbackServicer bearer authentication.

Verifies that RequestTokenRefresh rejects calls with missing or wrong
x-backend-bearer metadata, and accepts calls with the correct derived value.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc  # type: ignore[import-untyped]
import pytest

pytestmark = pytest.mark.no_db


def _make_bearer(key: str) -> str:
    return hashlib.sha256(f"backend_callback:{key}".encode()).hexdigest()


def _make_context(metadata: dict[str, str]) -> Any:
    ctx = MagicMock()
    # invocation_metadata() is synchronous in grpc
    ctx.invocation_metadata.return_value = list(metadata.items())
    ctx.abort = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_missing_bearer_aborts_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """No x-backend-bearer header → UNAUTHENTICATED abort."""
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    from app.services.broker_callback_server import BackendCallbackServicer

    servicer = BackendCallbackServicer(
        config_service=MagicMock(),
        db_session_factory=MagicMock(),
    )

    from app._generated.broker.v1 import broker_pb2 as pb

    request = pb.TokenRefreshRequest(broker_id="schwab")
    context = _make_context({})  # no bearer header

    await servicer.RequestTokenRefresh(request, context)

    context.abort.assert_awaited_once()
    call_args = context.abort.call_args
    assert call_args[0][0] == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_wrong_bearer_aborts_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong x-backend-bearer value → UNAUTHENTICATED abort."""
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    from app.services.broker_callback_server import BackendCallbackServicer

    servicer = BackendCallbackServicer(
        config_service=MagicMock(),
        db_session_factory=MagicMock(),
    )

    from app._generated.broker.v1 import broker_pb2 as pb

    request = pb.TokenRefreshRequest(broker_id="schwab")
    context = _make_context({"x-backend-bearer": "wrong-value"})

    await servicer.RequestTokenRefresh(request, context)

    context.abort.assert_awaited_once()
    call_args = context.abort.call_args
    assert call_args[0][0] == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_correct_bearer_passes_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Correct bearer + unknown broker_id → INVALID_ARGUMENT (auth gate passed)."""
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    from app.services.broker_callback_server import BackendCallbackServicer

    servicer = BackendCallbackServicer(
        config_service=MagicMock(),
        db_session_factory=MagicMock(),
    )

    from app._generated.broker.v1 import broker_pb2 as pb

    bearer = _make_bearer("test-secret")
    request = pb.TokenRefreshRequest(broker_id="unknown_broker")
    context = _make_context({"x-backend-bearer": bearer})

    await servicer.RequestTokenRefresh(request, context)

    context.abort.assert_awaited_once()
    call_args = context.abort.call_args
    # Auth gate passed — reached broker_id check, which rejects with INVALID_ARGUMENT
    assert call_args[0][0] == grpc.StatusCode.INVALID_ARGUMENT


def test_backend_callback_bearer_derives_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different APP_SECRET_KEY values produce two different 64-char hex bearers."""
    monkeypatch.setenv("APP_SECRET_KEY", "key-one")
    # Force reimport of the function to pick up monkeypatched env
    import importlib

    from app.services import broker_callback_server

    importlib.reload(broker_callback_server)
    bearer_one = broker_callback_server._backend_callback_bearer()

    monkeypatch.setenv("APP_SECRET_KEY", "key-two")
    bearer_two = broker_callback_server._backend_callback_bearer()

    assert len(bearer_one) == 64  # SHA-256 hex
    assert len(bearer_two) == 64
    assert bearer_one != bearer_two
