"""Phase 9 — GetHistoricalBars handler maps Futu kline rows to proto bars."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn
from unittest.mock import MagicMock

import futu as ft
import grpc
import pandas as pd
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers

pytestmark = [pytest.mark.unit]


class AbortContext:
    def __init__(self) -> None:
        self.aborted: tuple[grpc.StatusCode, str] | None = None

    async def abort(self, code: grpc.StatusCode, detail: str) -> NoReturn:
        self.aborted = (code, detail)
        raise grpc.RpcError(detail)


def _ts(value: str) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.fromisoformat(value).replace(tzinfo=UTC))
    return ts


def _request(
    *,
    canonical_id: str = "0700.HK",
    timeframe: str = "1m",
    limit: int = 500,
) -> broker_pb2.GetHistoricalBarsRequest:
    return broker_pb2.GetHistoricalBarsRequest(
        canonical_id=canonical_id,
        timeframe=timeframe,
        range_start=_ts("2026-05-07T01:30:00"),
        range_end=_ts("2026-05-07T01:31:00"),
        limit=limit,
    )


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_key": "2026-05-07 09:30:00",
                "open": 400.1,
                "high": 401.2,
                "low": 399.9,
                "close": 400.8,
                "volume": 12345,
                "turnover": 4930000.12,
            }
        ]
    )


def _handlers_with_quote_ctx(
    ret: int = ft.RET_OK,
    df: pd.DataFrame | str | None = None,
    page_req_key: str | None = None,
) -> tuple[BrokerHandlers, MagicMock]:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    quote_ctx = MagicMock()
    quote_ctx.request_history_kline.return_value = (
        ret,
        _df() if df is None else df,
        page_req_key,
    )
    handlers._quote_ctx = quote_ctx
    return handlers, quote_ctx


@pytest.mark.asyncio
async def test_returns_parsed_hk_response() -> None:
    handlers, quote_ctx = _handlers_with_quote_ctx()

    response = await handlers.GetHistoricalBars(_request(), context=AbortContext())

    assert len(response.bars) == 1
    assert response.truncated is False
    expected_bucket_start = int(pd.Timestamp("2026-05-07 09:30:00").timestamp())
    assert response.bars[0].bucket_start.seconds == expected_bucket_start
    assert response.bars[0].open == "400.1"
    assert response.bars[0].high == "401.2"
    assert response.bars[0].low == "399.9"
    assert response.bars[0].close == "400.8"
    assert response.bars[0].volume == "12345"
    assert response.bars[0].trade_count == 0
    quote_ctx.request_history_kline.assert_called_once_with(
        "HK.00700",
        start="2026-05-07 01:30:00",
        end="2026-05-07 01:31:00",
        ktype=ft.KLType.K_1M,
        autype=ft.AuType.QFQ,
        max_count=500,
    )


@pytest.mark.asyncio
async def test_non_hk_canonical_id_aborts_unimplemented() -> None:
    handlers, _quote_ctx = _handlers_with_quote_ctx()
    ctx = AbortContext()

    with pytest.raises(grpc.RpcError):
        await handlers.GetHistoricalBars(
            _request(canonical_id="AAPL.US"),
            context=ctx,
        )

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.UNIMPLEMENTED
    assert ctx.aborted[1] == "futu sidecar supports HK instruments only"


@pytest.mark.asyncio
async def test_invalid_timeframe_aborts() -> None:
    handlers, _quote_ctx = _handlers_with_quote_ctx()
    ctx = AbortContext()

    with pytest.raises(grpc.RpcError):
        await handlers.GetHistoricalBars(_request(timeframe="5m"), context=ctx)

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_truncated_when_page_req_key_set() -> None:
    handlers, _quote_ctx = _handlers_with_quote_ctx(page_req_key="next-page")

    response = await handlers.GetHistoricalBars(_request(), context=AbortContext())

    assert response.truncated is True


@pytest.mark.asyncio
async def test_futu_error_aborts_unavailable() -> None:
    handlers, _quote_ctx = _handlers_with_quote_ctx(
        ret=ft.RET_ERROR,
        df="rate limited",
    )
    ctx = AbortContext()

    with pytest.raises(grpc.RpcError):
        await handlers.GetHistoricalBars(_request(), context=ctx)

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.UNAVAILABLE
    assert ctx.aborted[1] == "rate limited"
