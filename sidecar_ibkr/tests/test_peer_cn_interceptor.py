"""Tests for PeerCnInterceptor (H2: mTLS peer CN allowlist enforcement)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import grpc  # type: ignore[import-untyped]
import pytest

from sidecar_ibkr.tls import PeerCnInterceptor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(cn_bytes_list: list[bytes]) -> MagicMock:
    """Return a mock ServicerContext whose auth_context returns the given CNs."""
    ctx = MagicMock()
    ctx.auth_context.return_value = {"x509_common_name": cn_bytes_list}
    ctx.abort = AsyncMock()
    return ctx


def _make_handler(call_result: object = "ok") -> MagicMock:
    """Return a mock RpcMethodHandler with a unary_unary implementation."""
    handler = MagicMock()
    handler.unary_unary = AsyncMock(return_value=call_result)
    handler.unary_stream = None
    handler.stream_unary = None
    handler.stream_stream = None

    def _replace(**kwargs: object) -> MagicMock:
        new_handler = MagicMock()
        for attr in ("unary_unary", "unary_stream", "stream_unary", "stream_stream"):
            setattr(new_handler, attr, kwargs.get(attr, getattr(handler, attr)))
        new_handler._replace = _replace
        return new_handler

    handler._replace = _replace
    return handler


async def _run_interceptor(
    interceptor: PeerCnInterceptor,
    handler: MagicMock,
    context: MagicMock,
    request: object = None,
) -> object:
    """Drive the interceptor through continuation and call the wrapped handler."""

    async def _continuation(details: object) -> MagicMock:
        return handler

    call_details = MagicMock()
    wrapped_handler = await interceptor.intercept_service(_continuation, call_details)
    return await wrapped_handler.unary_unary(request, context)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_cn_passes_through() -> None:
    """Peer CN matching the allowlist must not abort; inner handler is called."""
    interceptor = PeerCnInterceptor(frozenset({"backend"}))
    handler = _make_handler(call_result="response")
    context = _make_context([b"backend"])

    result = await _run_interceptor(interceptor, handler, context)

    context.abort.assert_not_called()
    assert result == "response"


@pytest.mark.asyncio
async def test_disallowed_cn_aborts() -> None:
    """Peer CN not in the allowlist must abort with PERMISSION_DENIED."""
    interceptor = PeerCnInterceptor(frozenset({"backend"}))
    handler = _make_handler()
    context = _make_context([b"attacker"])

    await _run_interceptor(interceptor, handler, context)

    context.abort.assert_awaited_once_with(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")


@pytest.mark.asyncio
async def test_empty_allowlist_passes_all_through() -> None:
    """Empty expected_cns disables the check; handler returned unmodified."""
    interceptor = PeerCnInterceptor(frozenset())
    handler = _make_handler(call_result="open")
    context = _make_context([b"anyone"])

    async def _continuation(details: object) -> MagicMock:
        return handler

    call_details = MagicMock()
    wrapped_handler = await interceptor.intercept_service(_continuation, call_details)

    # When disabled, the handler is returned as-is (no wrapper injected).
    assert wrapped_handler is handler
    context.abort.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_cns_any_match_passes() -> None:
    """If the cert carries multiple CNs, matching any one is sufficient."""
    interceptor = PeerCnInterceptor(frozenset({"backend", "sidecar"}))
    handler = _make_handler(call_result="ok")
    context = _make_context([b"other", b"sidecar"])

    result = await _run_interceptor(interceptor, handler, context)

    context.abort.assert_not_called()
    assert result == "ok"


@pytest.mark.asyncio
async def test_missing_x509_common_name_aborts() -> None:
    """auth_context without x509_common_name key must be rejected."""
    interceptor = PeerCnInterceptor(frozenset({"backend"}))
    handler = _make_handler()
    context = MagicMock()
    context.auth_context.return_value = {}  # no x509_common_name key
    context.abort = AsyncMock()

    await _run_interceptor(interceptor, handler, context)

    context.abort.assert_awaited_once_with(grpc.StatusCode.PERMISSION_DENIED, "peer cn not allowed")
