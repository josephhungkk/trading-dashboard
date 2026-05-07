"""GetHistoricalBars coverage for Alpaca market-data RPCs."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2

pytestmark = [pytest.mark.unit]


class FakeBarsRequest:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeTimeFrame:
    Minute = "1Min"


class FakeContext:
    code: grpc.StatusCode | None = None
    details: str | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.code = code
        self.details = details
        raise grpc.RpcError(details)


def _timestamp(seconds: int) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromSeconds(seconds)
    return timestamp


def _request(
    canonical_id: str,
    *,
    timeframe: str = "1m",
) -> broker_pb2.GetHistoricalBarsRequest:
    return broker_pb2.GetHistoricalBarsRequest(
        canonical_id=canonical_id,
        timeframe=timeframe,
        range_start=_timestamp(1_700_000_000),
        range_end=_timestamp(1_700_000_060),
        limit=2,
    )


async def _servicer() -> AlpacaServicer:
    auth = AuthCache()
    await auth.set_credentials("key", "secret")
    return AlpacaServicer(auth_cache=auth)


@pytest.fixture
def historical_classes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    stock_client = SimpleNamespace(get_stock_bars=AsyncMock())
    crypto_client = SimpleNamespace(get_crypto_bars=AsyncMock())
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.StockHistoricalDataClient",
        lambda _key, _secret: stock_client,
    )
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.CryptoHistoricalDataClient",
        lambda _key, _secret: crypto_client,
    )
    monkeypatch.setattr("sidecar_alpaca.handlers.StockBarsRequest", FakeBarsRequest)
    monkeypatch.setattr("sidecar_alpaca.handlers.CryptoBarsRequest", FakeBarsRequest)
    monkeypatch.setattr("sidecar_alpaca.handlers.TimeFrame", FakeTimeFrame)
    return {"stock": stock_client, "crypto": crypto_client}


@pytest.mark.asyncio
async def test_equity_returns_parsed_response(
    historical_classes: dict[str, Any],
) -> None:
    bar = SimpleNamespace(
        timestamp=datetime.fromtimestamp(1_700_000_000, UTC),
        open=1.1,
        high=1.2,
        low=1.0,
        close=1.15,
        volume=100,
        trade_count=7,
    )
    historical_classes["stock"].get_stock_bars.return_value = SimpleNamespace(
        data={"AAPL": [bar]},
    )
    svc = await _servicer()

    response = await svc.GetHistoricalBars(
        _request("AAPL.US"),
        cast(grpc.aio.ServicerContext, FakeContext()),
    )

    request_arg = historical_classes["stock"].get_stock_bars.call_args.args[0]
    assert request_arg.kwargs["symbol_or_symbols"] == ["AAPL"]
    assert request_arg.kwargs["timeframe"] == FakeTimeFrame.Minute
    assert response.truncated is False
    assert len(response.bars) == 1
    assert response.bars[0].bucket_start.seconds == 1_700_000_000
    assert response.bars[0].open == "1.1"
    assert response.bars[0].high == "1.2"
    assert response.bars[0].low == "1.0"
    assert response.bars[0].close == "1.15"
    assert response.bars[0].volume == "100"
    assert response.bars[0].trade_count == 7


@pytest.mark.asyncio
async def test_crypto_routes_to_crypto_client(
    historical_classes: dict[str, Any],
) -> None:
    bar = SimpleNamespace(
        timestamp=datetime.fromtimestamp(1_700_000_000, UTC),
        open="10",
        high="11",
        low="9",
        close="10.5",
        volume="3.25",
        trade_count=None,
    )
    historical_classes["crypto"].get_crypto_bars.return_value = SimpleNamespace(
        data={"BTC/USD": [bar]},
    )
    svc = await _servicer()

    response = await svc.GetHistoricalBars(
        _request("BTC/USD"),
        cast(grpc.aio.ServicerContext, FakeContext()),
    )

    historical_classes["stock"].get_stock_bars.assert_not_called()
    request_arg = historical_classes["crypto"].get_crypto_bars.call_args.args[0]
    assert request_arg.kwargs["symbol_or_symbols"] == ["BTC/USD"]
    assert response.bars[0].trade_count == 0


@pytest.mark.asyncio
async def test_invalid_timeframe_aborts(
    historical_classes: dict[str, Any],
) -> None:
    svc = await _servicer()
    context = FakeContext()

    with pytest.raises(grpc.RpcError, match="timeframe_1m_only"):
        await svc.GetHistoricalBars(
            _request("AAPL.US", timeframe="5m"),
            cast(grpc.aio.ServicerContext, context),
        )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_truncated_flag_when_next_page_token(
    historical_classes: dict[str, Any],
) -> None:
    historical_classes["stock"].get_stock_bars.return_value = SimpleNamespace(
        data={"AAPL": []},
        next_page_token="next",
    )
    svc = await _servicer()

    response = await svc.GetHistoricalBars(
        _request("AAPL.US"),
        cast(grpc.aio.ServicerContext, FakeContext()),
    )

    assert response.truncated is True


@pytest.mark.asyncio
async def test_unknown_asset_class_aborts(
    historical_classes: dict[str, Any],
) -> None:
    svc = await _servicer()
    context = FakeContext()

    with pytest.raises(grpc.RpcError, match="unsupported_asset_class"):
        await svc.GetHistoricalBars(
            _request("NOT-CANONICAL"),
            cast(grpc.aio.ServicerContext, context),
        )

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
