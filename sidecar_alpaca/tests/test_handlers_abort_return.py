"""CRIT-code-1..4 coverage: abort fall-through fixes in AlpacaServicer.

Tests verify that after context.abort() is called, execution does NOT continue
into downstream logic (no double-submit, no AttributeError on None client, no
counter inflation). Four CRIT sites covered:

- CRIT-code-1: PlaceOrder dedupe abort returns immediately.
- CRIT-code-2: GetHistoricalBars validation aborts return immediately (5 sites).
- CRIT-code-3: _configured_trading_client None raises _ConfiguredClientUnavailable;
               PlaceOrder/CancelOrder/ModifyOrder/PlaceBracket catch and return.
- CRIT-code-4: _acquire_trading_stream RESOURCE_EXHAUSTED abort does not increment
               counter (tested separately in test_handlers_trading_stream_counter.py).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AbortContext:
    """Fake gRPC context that records abort calls without raising.

    grpc.aio's real context.abort() does NOT raise by itself — it marks the
    RPC as aborted and returns. Handlers must therefore explicitly return after
    every abort call. This fake replicates that behaviour so abort fall-through
    bugs are detectable.
    """

    def __init__(self) -> None:
        self.aborted: list[tuple[grpc.StatusCode, str]] = []

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.aborted.append((code, details))
        # Intentionally does NOT raise — mirrors grpc.aio's real behaviour.

    def set_code(self, code: grpc.StatusCode) -> None:
        pass

    def set_details(self, details: str) -> None:
        pass


def _ts(seconds: int) -> Timestamp:
    t = Timestamp()
    t.FromSeconds(seconds)
    return t


def _place_request(
    *,
    conid: str = "AAPL",
    side: str = "buy",
    qty: str = "1",
    client_order_id: str = "",
    account_number: str = "ACC1",
) -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        conid=conid,
        side=side,
        order_type="market",
        tif="day",
        qty=qty,
        client_order_id=client_order_id,
        account_number=account_number,
    )


def _bars_request(
    *,
    canonical_id: str = "stock:AAPL:US",
    timeframe: str = "1m",
    range_start: int = 1_700_000_000,
    range_end: int = 1_700_000_060,
) -> broker_pb2.GetHistoricalBarsRequest:
    return broker_pb2.GetHistoricalBarsRequest(
        canonical_id=canonical_id,
        timeframe=timeframe,
        range_start=_ts(range_start),
        range_end=_ts(range_end),
    )


async def _servicer() -> AlpacaServicer:
    from sidecar_alpaca.auth import AuthCache

    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return AlpacaServicer(auth_cache=auth)


# ---------------------------------------------------------------------------
# CRIT-code-1: PlaceOrder dedupe abort does not fall through to submit_order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_dedupe_abort_no_fallthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After dedupe abort, submit_order must NOT be called."""
    import sidecar_alpaca.config as _config

    servicer = await _servicer()
    monkeypatch.setattr(_config, "USE_IN_MEMORY_DEDUPE", True)

    submitted: list[Any] = []

    class FakeClient:
        def submit_order(self, req: Any) -> Any:
            submitted.append(req)
            return SimpleNamespace(id="ord1", status="new")

    monkeypatch.setattr(
        "sidecar_alpaca.handlers.get_trading_client",
        lambda **_: FakeClient(),
    )
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: {"MARKET": MagicMock(return_value=SimpleNamespace())},
    )

    ctx = _AbortContext()
    req = _place_request(client_order_id="DEDUPETEST1")

    # First call seeds the dedupe dict (returns False = not duplicate).
    servicer._place_order_is_duplicate(req)
    # Second call — same key, now a duplicate (returns True).
    resp = await servicer.PlaceOrder(req, ctx)  # type: ignore[arg-type]

    assert len(ctx.aborted) == 1
    assert ctx.aborted[0][0] == grpc.StatusCode.ALREADY_EXISTS
    assert submitted == [], f"submit_order was called despite abort: {submitted}"
    assert resp == broker_pb2.PlaceOrderResponse()


# ---------------------------------------------------------------------------
# CRIT-code-3: unconfigured trading client — callers catch and return empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_unconfigured_client_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sidecar_alpaca.config as _config

    servicer = await _servicer()
    monkeypatch.setattr(_config, "USE_IN_MEMORY_DEDUPE", False)
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.get_trading_client", lambda **_: None
    )
    ctx = _AbortContext()
    resp = await servicer.PlaceOrder(_place_request(), ctx)  # type: ignore[arg-type]
    assert any(code == grpc.StatusCode.NOT_FOUND for code, _ in ctx.aborted)
    assert resp == broker_pb2.PlaceOrderResponse()


@pytest.mark.asyncio
async def test_cancel_order_unconfigured_client_returns_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.get_trading_client", lambda **_: None
    )
    ctx = _AbortContext()
    req = broker_pb2.CancelOrderRequest(
        account_number="ACC1", broker_order_id="ORD1"
    )
    resp = await servicer.CancelOrder(req, ctx)  # type: ignore[arg-type]
    assert any(code == grpc.StatusCode.NOT_FOUND for code, _ in ctx.aborted)
    assert not resp.accepted


@pytest.mark.asyncio
async def test_modify_order_unconfigured_client_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.get_trading_client", lambda **_: None
    )
    ctx = _AbortContext()
    req = broker_pb2.ModifyOrderRequest(
        account_number="ACC1",
        broker_order_id="ORD1",
        qty="10",
    )
    resp = await servicer.ModifyOrder(req, ctx)  # type: ignore[arg-type]
    assert any(code == grpc.StatusCode.NOT_FOUND for code, _ in ctx.aborted)
    assert resp == broker_pb2.ModifyOrderResponse()


@pytest.mark.asyncio
async def test_place_bracket_unconfigured_client_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.get_trading_client", lambda **_: None
    )
    ctx = _AbortContext()
    req = broker_pb2.PlaceBracketRequest(
        parent=broker_pb2.PlaceOrderRequest(
            account_number="ACC1",
            conid="AAPL",
            side="buy",
            order_type="market",
            tif="day",
            qty="1",
        )
    )
    resp = await servicer.PlaceBracket(req, ctx)  # type: ignore[arg-type]
    assert any(code == grpc.StatusCode.NOT_FOUND for code, _ in ctx.aborted)
    assert resp == broker_pb2.PlaceBracketResponse()


# ---------------------------------------------------------------------------
# CRIT-code-2: GetHistoricalBars — each of 5 validation aborts returns early
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_historical_bars_wrong_timeframe_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    fetch_called: list[Any] = []
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.AlpacaServicer._configured_market_data_clients",
        AsyncMock(side_effect=lambda ctx: fetch_called.append(1)),
    )
    ctx = _AbortContext()
    resp = await servicer.GetHistoricalBars(  # type: ignore[arg-type]
        _bars_request(timeframe="5m"), ctx
    )
    assert any(code == grpc.StatusCode.INVALID_ARGUMENT for code, _ in ctx.aborted)
    assert fetch_called == [], "data client was called despite validation abort"
    assert resp == broker_pb2.GetHistoricalBarsResponse()


@pytest.mark.asyncio
async def test_get_historical_bars_blank_canonical_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    fetch_called: list[Any] = []
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.AlpacaServicer._configured_market_data_clients",
        AsyncMock(side_effect=lambda ctx: fetch_called.append(1)),
    )
    ctx = _AbortContext()
    resp = await servicer.GetHistoricalBars(  # type: ignore[arg-type]
        _bars_request(canonical_id="   "), ctx
    )
    assert any(code == grpc.StatusCode.INVALID_ARGUMENT for code, _ in ctx.aborted)
    assert fetch_called == []
    assert resp == broker_pb2.GetHistoricalBarsResponse()


@pytest.mark.asyncio
async def test_get_historical_bars_zero_timestamps_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    fetch_called: list[Any] = []
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.AlpacaServicer._configured_market_data_clients",
        AsyncMock(side_effect=lambda ctx: fetch_called.append(1)),
    )
    ctx = _AbortContext()
    req = broker_pb2.GetHistoricalBarsRequest(
        canonical_id="stock:AAPL:US",
        timeframe="1m",
        range_start=_ts(0),
        range_end=_ts(0),
    )
    resp = await servicer.GetHistoricalBars(req, ctx)  # type: ignore[arg-type]
    assert any(code == grpc.StatusCode.INVALID_ARGUMENT for code, _ in ctx.aborted)
    assert fetch_called == []
    assert resp == broker_pb2.GetHistoricalBarsResponse()


@pytest.mark.asyncio
async def test_get_historical_bars_inverted_range_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    fetch_called: list[Any] = []
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.AlpacaServicer._configured_market_data_clients",
        AsyncMock(side_effect=lambda ctx: fetch_called.append(1)),
    )
    ctx = _AbortContext()
    resp = await servicer.GetHistoricalBars(  # type: ignore[arg-type]
        _bars_request(range_start=1_700_000_060, range_end=1_700_000_000), ctx
    )
    assert any(code == grpc.StatusCode.INVALID_ARGUMENT for code, _ in ctx.aborted)
    assert fetch_called == []
    assert resp == broker_pb2.GetHistoricalBarsResponse()


@pytest.mark.asyncio
async def test_get_historical_bars_unknown_asset_class_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servicer = await _servicer()
    fetch_called: list[Any] = []
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.AlpacaServicer._configured_market_data_clients",
        AsyncMock(side_effect=lambda ctx: fetch_called.append(1)),
    )
    ctx = _AbortContext()
    # canonical_id with no recognised prefix → _historical_asset_class returns None
    resp = await servicer.GetHistoricalBars(  # type: ignore[arg-type]
        _bars_request(canonical_id="unknown:FOO"), ctx
    )
    assert any(code == grpc.StatusCode.INVALID_ARGUMENT for code, _ in ctx.aborted)
    assert fetch_called == []
    assert resp == broker_pb2.GetHistoricalBarsResponse()
