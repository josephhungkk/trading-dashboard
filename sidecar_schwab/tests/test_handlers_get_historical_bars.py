"""GetHistoricalBars handler tests."""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from requests import HTTPError

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer

pytestmark = [pytest.mark.unit]

_FIXTURE = Path(__file__).parent / "fixtures" / "aapl_30d_1m.csv"


def _load_candles() -> list[dict[str, str]]:
    with _FIXTURE.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _request(
    *,
    timeframe: str = "1m",
    limit: int = 0,
) -> broker_pb2.GetHistoricalBarsRequest:
    return broker_pb2.GetHistoricalBarsRequest(
        canonical_id="AAPL.US",
        timeframe=timeframe,
        range_start=Timestamp(seconds=1_777_645_800),
        range_end=Timestamp(seconds=1_777_648_740),
        limit=limit,
    )


def _build_servicer(
    *,
    payload: dict[str, list[dict[str, str]]] | None = None,
) -> BrokerServicer:
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.ensure_fresh_token = AsyncMock()
    candles_payload = payload if payload is not None else {"candles": _load_candles()}
    # Mimic schwabdev response shape: object with .status_code + .json() rather than
    # the bare dict that bypasses the production status/json code path.
    servicer._client.price_history = MagicMock(
        return_value=SimpleNamespace(status_code=200, json=lambda: candles_payload)
    )
    return servicer


def _http_401() -> HTTPError:
    exc = HTTPError("invalid_token")
    exc.response = SimpleNamespace(status_code=401)
    return exc


@pytest.mark.asyncio
async def test_returns_parsed_response() -> None:
    servicer = _build_servicer()

    response = await servicer.GetHistoricalBars(_request(), MagicMock())

    assert len(response.bars) == 50
    assert response.truncated is False
    first = response.bars[0]
    assert first.bucket_start.seconds == 1_777_645_800
    assert first.open == "180.00"
    assert first.high == "180.03"
    assert first.low == "179.98"
    assert first.close == "180.01"
    assert first.volume == "1000"
    assert first.trade_count == 0
    servicer._client.price_history.assert_called_once_with(
        symbol="AAPL",
        periodType="day",
        frequencyType="minute",
        frequency=1,
        startDate=1_777_645_800_000,
        endDate=1_777_648_740_000,
        needExtendedHoursData=True,
    )


@pytest.mark.asyncio
async def test_401_retries_once() -> None:
    candles = _load_candles()
    servicer = _build_servicer(payload={"candles": candles})
    servicer._client.price_history = AsyncMock(
        side_effect=[_http_401(), {"candles": candles}]
    )

    response = await servicer.GetHistoricalBars(_request(), MagicMock())

    assert len(response.bars) == 50
    assert servicer._client.price_history.call_count == 2
    servicer._client.ensure_fresh_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_401_persists_aborts() -> None:
    servicer = _build_servicer()
    servicer._client.price_history = AsyncMock(side_effect=[_http_401(), _http_401()])
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))

    with pytest.raises(Exception, match="aborted"):
        await servicer.GetHistoricalBars(_request(), ctx)

    assert servicer._client.price_history.call_count == 2
    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_invalid_timeframe_aborts() -> None:
    servicer = _build_servicer()
    ctx = MagicMock()
    ctx.abort = AsyncMock(side_effect=Exception("aborted"))

    with pytest.raises(Exception, match="aborted"):
        await servicer.GetHistoricalBars(_request(timeframe="5m"), ctx)

    code, _msg = ctx.abort.call_args[0]
    assert code == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_truncated_flag_when_at_limit() -> None:
    candles = _load_candles()
    servicer = _build_servicer(payload={"candles": candles})

    response = await servicer.GetHistoricalBars(
        _request(limit=len(candles)),
        MagicMock(),
    )

    assert len(response.bars) == len(candles)
    assert response.truncated is True
