"""Phase 7b.1 Schwab streamer smoke tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.streamer import SchwabStreamer, _SymbolEntry


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._recv_event = asyncio.Event()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        await self._recv_event.wait()
        return "{}"

    async def close(self) -> None:
        self.closed = True


class FakeHistogram:
    def __init__(self) -> None:
        self.observed: list[float] = []

    def observe(self, value: float) -> None:
        self.observed.append(value)


def _streamer(ws: FakeWebSocket | None = None) -> SchwabStreamer:
    streamer = SchwabStreamer.for_tests()
    streamer._ws = ws or FakeWebSocket()
    streamer._streamer_info = {
        "schwabClientCustomerId": "cust",
        "schwabClientCorrelId": "corr",
    }
    return streamer


def _sym(canonical_id: str, raw_symbol: str | None = None) -> pb.SymbolRef:
    return pb.SymbolRef(
        canonical_id=canonical_id,
        raw_symbol=raw_symbol or canonical_id,
        asset_class=pb.STOCK,
        exchange="US",
        currency="USD",
    )


def _commands(ws: FakeWebSocket) -> list[str]:
    return [frame["requests"][0]["command"] for frame in ws.sent]


@pytest.mark.asyncio
async def test_on_subscribe_first_ref_sends_subs() -> None:
    ws = FakeWebSocket()
    streamer = _streamer(ws)

    await streamer.on_subscribe([_sym("AAPL")])

    req = ws.sent[0]["requests"][0]
    assert req["command"] == "SUBS"
    assert req["service"] == "LEVELONE_EQUITIES"
    assert req["parameters"]["keys"] == "AAPL"
    assert _commands(ws) == ["SUBS", "ADD"]


@pytest.mark.asyncio
async def test_on_subscribe_second_ref_sends_add() -> None:
    ws = FakeWebSocket()
    streamer = _streamer(ws)
    await streamer.on_subscribe([_sym("AAPL")])

    await streamer.on_subscribe([_sym("AAPL")])

    assert _commands(ws) == ["SUBS", "ADD", "ADD"]


@pytest.mark.asyncio
async def test_on_unsubscribe_last_ref_sends_unsubs() -> None:
    ws = FakeWebSocket()
    streamer = _streamer(ws)
    await streamer.on_subscribe([_sym("AAPL")])

    await streamer.on_unsubscribe([_sym("AAPL")])

    req = ws.sent[-1]["requests"][0]
    assert req["command"] == "UNSUBS"
    assert req["parameters"]["keys"] == "AAPL"


@pytest.mark.asyncio
async def test_on_resync_sends_diff() -> None:
    ws = FakeWebSocket()
    streamer = _streamer(ws)
    streamer._upstream_refcount = {
        "A": _SymbolEntry("A", 1),
        "B": _SymbolEntry("B", 1),
    }

    await streamer.on_resync([_sym("B"), _sym("C")])

    assert _commands(ws) == ["SUBS", "UNSUBS"]
    assert ws.sent[0]["requests"][0]["parameters"]["keys"] == "C"
    assert ws.sent[1]["requests"][0]["parameters"]["keys"] == "A"
    assert set(streamer._upstream_refcount) == {"B", "C"}


@pytest.mark.asyncio
async def test_token_rotation_breaks_recv_loop_and_reconnects(monkeypatch) -> None:
    ws = FakeWebSocket()
    event = asyncio.Event()
    streamer = SchwabStreamer.for_tests(event)
    streamer._ws = ws
    histogram = FakeHistogram()
    closed = False
    reconnected = False

    async def close_ws() -> None:
        nonlocal closed
        closed = True
        await ws.close()

    async def reconnect() -> bool:
        nonlocal reconnected
        reconnected = True
        streamer._shutting_down = True
        return True

    monkeypatch.setattr(
        "sidecar_schwab.streamer.SCHWAB_STREAMER_TOKEN_ROTATION_GAP_SECONDS", histogram
    )
    monkeypatch.setattr(streamer, "_close_ws", close_ws)
    monkeypatch.setattr(streamer, "_reconnect_with_new_creds", reconnect)

    event.set()
    await streamer._reader_loop()

    assert closed is True
    assert reconnected is True
    assert ws.closed is True
    assert histogram.observed


def test_tick_callback_fires_with_quote_message() -> None:
    streamer = _streamer()
    received: list[pb.QuoteMessage] = []
    streamer.tick_callback = received.append
    streamer._upstream_refcount = {
        "canon:AAPL": _SymbolEntry("AAPL", 1),
    }
    frame = {
        "data": [
            {
                "service": "LEVELONE_EQUITIES",
                "content": [
                    {
                        "0": "AAPL",
                        "1": 180.1,
                        "2": "180.2",
                        "3": "180.15",
                        "8": 123,
                        "12": "179",
                        "28": "181",
                        "29": "178",
                        "30": "179.5",
                        "33": "0.64",
                    }
                ],
            }
        ]
    }

    streamer._dispatch_frame(json.dumps(frame))

    assert len(received) == 1
    quote = received[0]
    assert quote.canonical_id == "canon:AAPL"
    assert quote.source == "schwab"
    assert quote.bid == "180.1"
    assert quote.ask == "180.2"
    assert quote.last == "180.15"
    assert quote.volume == "123"
    assert quote.prev_close == "179"
    assert quote.day_high == "181"
    assert quote.day_low == "178"
    assert quote.open == "179.5"
    assert quote.change_pct == "0.64"
    assert quote.received_at.seconds > 0


def test_levelone_equities_golden_trace_emits_quote_messages() -> None:
    fixture_path = (
        __import__("pathlib").Path(__file__).parent
        / "golden"
        / "levelone_equities_aapl_spx.json"
    )
    payload = json.loads(
        fixture_path.read_text(), parse_float=__import__("decimal").Decimal
    )
    streamer = SchwabStreamer.for_tests()
    received: list[pb.QuoteMessage] = []
    streamer.tick_callback = received.append
    streamer._upstream_refcount = {
        "stock:AAPL:US": _SymbolEntry("AAPL", 1),
        "idx:$SPX:US": _SymbolEntry("$SPX", 1),
    }

    for frame in payload["frames"]:
        streamer._dispatch_frame(json.dumps(frame, default=str))

    assert len(received) == 2
    aapl = received[0]
    assert aapl.canonical_id == "stock:AAPL:US"
    assert aapl.last == "213.45"
    assert aapl.bid == "213.40"
    assert aapl.ask == "213.46"
    assert aapl.volume == "38291842"
    spx = received[1]
    assert spx.canonical_id == "idx:$SPX:US"
    assert spx.last == "5210.52"
