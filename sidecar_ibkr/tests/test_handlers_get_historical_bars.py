"""Tests for GetHistoricalBars and IBKR historical pacing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_ibkr._generated.broker.v1 import broker_pb2
from sidecar_ibkr.handlers import BrokerHandlers, _PacingTokenBucket, _instrument_id_hash
from sidecar_ibkr.pnl_cache import PnLCache

pytestmark = [pytest.mark.unit]


@dataclass
class FakeBar:
    date: int
    open: str
    high: str
    low: str
    close: str
    volume: str


class FakeIB:
    def __init__(self, bars: list[FakeBar] | None = None) -> None:
        self.reqHistoricalDataAsync = AsyncMock(return_value=bars or [])

    def isConnected(self) -> bool:  # noqa: N802
        return True


class AbortRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(code, details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise AbortRpcError(code, details)

    def invocation_metadata(self) -> list[object]:
        return []


def _timestamp(dt: datetime) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromDatetime(dt)
    return timestamp


def _request(
    canonical_id: str = "stock:AAPL:US",
    *,
    limit: int = 0,
) -> broker_pb2.GetHistoricalBarsRequest:
    return broker_pb2.GetHistoricalBarsRequest(
        canonical_id=canonical_id,
        timeframe="1m",
        range_start=_timestamp(datetime(2026, 5, 7, 14, 30, tzinfo=UTC)),
        range_end=_timestamp(datetime(2026, 5, 7, 14, 32, tzinfo=UTC)),
        limit=limit,
    )


def _handler(ib: FakeIB) -> BrokerHandlers:
    handler = BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        pnl_cache=PnLCache(ib),  # type: ignore[arg-type]
        label="ibgw_live_us",
        version="0.4.0+test",
        last_tick_ref={},
    )
    handler._pacing_bucket = SimpleNamespace(acquire=AsyncMock())  # type: ignore[assignment]
    return handler


def _canonical_id_with_jitter() -> str:
    for canonical_id in ("stock:AAPL:US", "stock:MSFT:US", "stock:NVDA:US", "stock:TSLA:US"):
        if _instrument_id_hash(canonical_id) % 4 != 0:
            return canonical_id
    raise AssertionError("expected at least one fixture symbol to have jitter")


@pytest.mark.asyncio
async def test_returns_parsed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [
        FakeBar(1_777_810_200, "100.1", "101.2", "99.9", "100.8", "1234"),
        FakeBar(1_777_810_260, "100.8", "102.0", "100.7", "101.5", "5678"),
    ]
    ib = FakeIB(bars)
    handler = _handler(ib)
    monkeypatch.setattr("sidecar_ibkr.handlers.asyncio.sleep", AsyncMock())

    response = await handler.GetHistoricalBars(
        _request(limit=1),
        FakeContext(),  # type: ignore[arg-type]
    )

    assert response.truncated is True
    assert len(response.bars) == 1
    assert response.bars[0].bucket_start.seconds == 1_777_810_200
    assert response.bars[0].open == "100.1"
    assert response.bars[0].high == "101.2"
    assert response.bars[0].low == "99.9"
    assert response.bars[0].close == "100.8"
    assert response.bars[0].volume == "1234"
    ib.reqHistoricalDataAsync.assert_awaited_once()
    kwargs = ib.reqHistoricalDataAsync.await_args.kwargs
    assert kwargs["barSizeSetting"] == "1 min"
    assert kwargs["whatToShow"] == "TRADES"
    assert kwargs["useRTH"] is False
    assert kwargs["formatDate"] == 2


@pytest.mark.asyncio
async def test_jittered_sleep_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("sidecar_ibkr.handlers.asyncio.sleep", sleep)
    ib = FakeIB([])
    handler = _handler(ib)
    canonical_id = _canonical_id_with_jitter()

    await handler.GetHistoricalBars(
        _request(canonical_id=canonical_id),
        FakeContext(),  # type: ignore[arg-type]
    )

    sleep.assert_awaited()
    assert any(call.args and call.args[0] > 0 for call in sleep.await_args_list)


@pytest.mark.asyncio
async def test_pacing_violation_aborts_with_resource_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ib = FakeIB([])
    ib.reqHistoricalDataAsync.side_effect = RuntimeError(
        "Historical data request pacing violation"
    )
    handler = _handler(ib)
    release = MagicMock()
    handler._pacing_bucket = SimpleNamespace(  # type: ignore[assignment]
        acquire=AsyncMock(),
        release_on_pacing_violation=release,
    )
    monkeypatch.setattr("sidecar_ibkr.handlers.asyncio.sleep", AsyncMock())

    with pytest.raises(AbortRpcError) as exc:
        await handler.GetHistoricalBars(_request(), FakeContext())  # type: ignore[arg-type]

    assert exc.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert exc.value.details() == "ibkr pacing violation; retry after 60s"
    release.assert_called_once()


@pytest.mark.asyncio
async def test_token_bucket_acquire_blocks_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("sidecar_ibkr.handlers.asyncio.sleep", sleep)
    bucket = _PacingTokenBucket(capacity=1, refill_window_seconds=600, reserved=0)

    await bucket.acquire(reserve=True)
    await bucket.acquire(reserve=True)

    sleep.assert_awaited()
    assert sleep.await_args.args[0] > 0


@pytest.mark.asyncio
async def test_token_bucket_reserve_floor_for_prewarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("sidecar_ibkr.handlers.asyncio.sleep", sleep)
    bucket = _PacingTokenBucket(capacity=2, refill_window_seconds=600, reserved=1)

    await bucket.acquire(reserve=False)
    await bucket.acquire(reserve=False)

    sleep.assert_awaited()
    assert sleep.await_args.args[0] > 0
