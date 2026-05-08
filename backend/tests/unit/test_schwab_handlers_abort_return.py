"""CRIT-2: CancelOrder + SearchContracts must return after context.abort().

Phase 8b fixed PlaceOrder + ModifyOrder. This batch fixes the remaining two RPCs.
Without the return, execution falls through to code that dereferences self._client
(CancelOrder) or `results` (SearchContracts), raising AttributeError /
UnboundLocalError.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

# Phase 9.7: this test imports from sidecar_schwab, whose proto stubs
# are generated only by the sidecar_schwab CI job (not the backend
# job). importorskip lets the backend pytest run cleanly when the
# schwab _generated/ directory isn't on sys.path; the schwab CI job
# still exercises this test.
pytest.importorskip("sidecar_schwab._generated.broker.v1.broker_pb2")
from sidecar_schwab.handlers import BrokerServicer


class _FakeContext:
    """Minimal grpc.aio.ServicerContext stub."""

    def __init__(self) -> None:
        self.aborted_code: grpc.StatusCode | None = None
        self.aborted_detail: str = ""
        self._abort_called = False

    async def abort(self, code: grpc.StatusCode, detail: str) -> None:
        self.aborted_code = code
        self.aborted_detail = detail
        self._abort_called = True

    @property
    def abort_called(self) -> bool:
        return self._abort_called


class _CancelReq:
    broker_order_id: str = "ORD-123"
    account_number: str = "ACC-1"


class _SearchReq:
    query: str = "AAPL"
    asset_class: int = 0  # ASSET_UNSPECIFIED


# ── CancelOrder ───────────────────────────────────────────────────────────────


def test_cancel_order_returns_after_abort_when_not_configured() -> None:
    """CRIT-2: _client is None → abort FAILED_PRECONDITION and return, not crash."""
    svc = BrokerServicer()
    ctx = _FakeContext()

    result = asyncio.run(svc.CancelOrder(_CancelReq(), ctx))

    assert ctx.abort_called
    assert ctx.aborted_code == grpc.StatusCode.FAILED_PRECONDITION
    assert result is not None  # CancelOrderResponse(), not AttributeError


def test_cancel_order_returns_after_abort_when_account_hash_missing() -> None:
    """CRIT-2: unknown account_number → abort NOT_FOUND and return, not crash."""
    svc = BrokerServicer()
    fake_client = MagicMock()
    fake_client.hash_for.return_value = ""
    svc._client = fake_client

    ctx = _FakeContext()

    result = asyncio.run(svc.CancelOrder(_CancelReq(), ctx))

    assert ctx.abort_called
    assert ctx.aborted_code == grpc.StatusCode.NOT_FOUND
    assert result is not None


# ── SearchContracts ───────────────────────────────────────────────────────────


def test_search_contracts_returns_after_abort_when_not_configured() -> None:
    """CRIT-2: _client is None → abort FAILED_PRECONDITION and return."""
    svc = BrokerServicer()
    ctx = _FakeContext()

    result = asyncio.run(svc.SearchContracts(_SearchReq(), ctx))

    assert ctx.abort_called
    assert ctx.aborted_code == grpc.StatusCode.FAILED_PRECONDITION
    assert result is not None


def test_search_contracts_returns_after_abort_on_http_error() -> None:
    """CRIT-2: HTTP 429 → abort RESOURCE_EXHAUSTED, no UnboundLocalError on `results`."""
    from sidecar_schwab.client import SchwabHTTPError

    svc = BrokerServicer()
    fake_client = MagicMock()
    fake_client.ensure_fresh_token = AsyncMock()
    fake_client.search_instruments = AsyncMock(
        side_effect=SchwabHTTPError("rate limited", status_code=429, endpoint="/instruments")
    )
    svc._client = fake_client

    ctx = _FakeContext()

    result = asyncio.run(svc.SearchContracts(_SearchReq(), ctx))

    assert ctx.abort_called
    assert ctx.aborted_code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert result is not None  # SearchContractsResponse(), not UnboundLocalError


def test_search_contracts_returns_after_abort_on_transport_error() -> None:
    """CRIT-2: OSError → abort UNAVAILABLE, no UnboundLocalError on `results`."""
    svc = BrokerServicer()
    fake_client = MagicMock()
    fake_client.ensure_fresh_token = AsyncMock()
    fake_client.search_instruments = AsyncMock(side_effect=OSError("connection reset"))
    svc._client = fake_client

    ctx = _FakeContext()

    result = asyncio.run(svc.SearchContracts(_SearchReq(), ctx))

    assert ctx.abort_called
    assert ctx.aborted_code == grpc.StatusCode.UNAVAILABLE
    assert result is not None
