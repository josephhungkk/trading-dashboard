"""Phase 7b.1 C2 - Schwab StreamQuotes handler wiring."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.auth import TokenCache
from sidecar_schwab.handlers import BrokerServicer


class FakeStreamer:
    def __init__(self) -> None:
        self.tick_callback = None
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.shutdown = AsyncMock()
        self.on_subscribe = AsyncMock()
        self.on_unsubscribe = AsyncMock()
        self.on_resync = AsyncMock()


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise grpc.RpcError(f"{code}: {details}")


def _symbol(canonical_id: str) -> pb.SymbolRef:
    return pb.SymbolRef(canonical_id=canonical_id, raw_symbol=canonical_id)


def _subscribe(*symbols: str) -> pb.StreamQuotesRequest:
    return pb.StreamQuotesRequest(
        subscribe=pb.StreamQuotesRequest.Subscribe(
            symbols=[_symbol(symbol) for symbol in symbols]
        )
    )


async def _request_stream(
    request: pb.StreamQuotesRequest,
    yielded: asyncio.Event,
    hold: asyncio.Event,
) -> AsyncIterator[pb.StreamQuotesRequest]:
    yield request
    yielded.set()
    await hold.wait()


def _fake_streamer() -> FakeStreamer:
    return FakeStreamer()


async def _first_message(
    servicer: BrokerServicer,
    request_iterator: AsyncIterator[pb.StreamQuotesRequest],
) -> pb.QuoteMessage:
    stream = servicer.StreamQuotes(request_iterator, FakeContext())
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_stream_quotes_yields_quote_after_subscribe(monkeypatch) -> None:
    servicer = BrokerServicer()
    streamer = _fake_streamer()
    subscribed = asyncio.Event()
    hold = asyncio.Event()
    yielded = asyncio.Event()

    async def on_subscribe(symbols: list[pb.SymbolRef]) -> None:
        assert [symbol.canonical_id for symbol in symbols] == ["AAPL"]
        subscribed.set()

    streamer.on_subscribe.side_effect = on_subscribe
    monkeypatch.setattr(
        servicer,
        "_get_or_init_schwab_streamer",
        AsyncMock(return_value=streamer),
    )

    message_task = asyncio.create_task(
        _first_message(servicer, _request_stream(_subscribe("AAPL"), yielded, hold))
    )
    await subscribed.wait()
    quote = pb.QuoteMessage(canonical_id="AAPL", last="193.50", source="schwab")
    streamer.tick_callback(quote)

    assert await message_task == quote
    streamer.on_subscribe.assert_awaited_once()
    hold.set()


@pytest.mark.asyncio
async def test_stream_quotes_disconnects_drops_subs(monkeypatch) -> None:
    servicer = BrokerServicer()
    streamer = _fake_streamer()
    subscribed = asyncio.Event()
    hold = asyncio.Event()
    yielded = asyncio.Event()

    async def on_subscribe(symbols: list[pb.SymbolRef]) -> None:
        assert {symbol.canonical_id for symbol in symbols} == {"AAPL", "MSFT"}
        subscribed.set()

    streamer.on_subscribe.side_effect = on_subscribe
    monkeypatch.setattr(
        servicer,
        "_get_or_init_schwab_streamer",
        AsyncMock(return_value=streamer),
    )

    stream = servicer.StreamQuotes(
        _request_stream(_subscribe("AAPL", "MSFT"), yielded, hold),
        FakeContext(),
    )
    task = asyncio.create_task(anext(stream))
    await subscribed.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await stream.aclose()
    await asyncio.sleep(0)

    cleanup_symbols = streamer.on_unsubscribe.await_args.args[0]
    assert {symbol.canonical_id for symbol in cleanup_symbols} == {"AAPL", "MSFT"}
    leaked = [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "schwab-stream-quotes-consumer" and not task.done()
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_stream_quotes_concurrent_calls_share_streamer(monkeypatch) -> None:
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._token_cache = TokenCache(refresh_client=AsyncMock())
    servicer._token_cache.set_tokens("A", "R", datetime.now(UTC))
    streamer = _fake_streamer()
    subscribed: asyncio.Queue[list[pb.SymbolRef]] = asyncio.Queue()

    async def on_subscribe(symbols: list[pb.SymbolRef]) -> None:
        await subscribed.put(symbols)

    streamer.on_subscribe.side_effect = on_subscribe
    schwab_streamer = MagicMock(return_value=streamer)
    monkeypatch.setattr("sidecar_schwab.streamer.SchwabStreamer", schwab_streamer)

    hold_1 = asyncio.Event()
    hold_2 = asyncio.Event()
    yielded_1 = asyncio.Event()
    yielded_2 = asyncio.Event()
    stream_1 = servicer.StreamQuotes(
        _request_stream(_subscribe("AAPL"), yielded_1, hold_1),
        FakeContext(),
    )
    stream_2 = servicer.StreamQuotes(
        _request_stream(_subscribe("MSFT"), yielded_2, hold_2),
        FakeContext(),
    )
    task_1 = asyncio.create_task(anext(stream_1))
    task_2 = asyncio.create_task(anext(stream_2))

    await subscribed.get()
    await subscribed.get()
    quote = pb.QuoteMessage(canonical_id="AAPL", last="1")
    streamer.tick_callback(quote)

    assert await task_1 == quote
    assert await task_2 == quote
    assert servicer._streamer is streamer
    schwab_streamer.assert_called_once()
    streamer.start.assert_awaited_once()

    await stream_1.aclose()
    streamer.stop.assert_not_called()
    streamer.shutdown.assert_not_called()
    await stream_2.aclose()


@pytest.mark.asyncio
async def test_streamer_singleton_lazy_init(monkeypatch) -> None:
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._token_cache = TokenCache(refresh_client=AsyncMock())
    servicer._token_cache.set_tokens("A", "R", datetime.now(UTC))
    streamer = _fake_streamer()

    async def start() -> None:
        await asyncio.sleep(0)

    streamer.start.side_effect = start
    schwab_streamer = MagicMock(return_value=streamer)
    monkeypatch.setattr("sidecar_schwab.streamer.SchwabStreamer", schwab_streamer)

    first, second = await asyncio.gather(
        servicer._get_or_init_schwab_streamer(),
        servicer._get_or_init_schwab_streamer(),
    )

    assert first is streamer
    assert second is streamer
    schwab_streamer.assert_called_once()
    streamer.start.assert_awaited_once()
