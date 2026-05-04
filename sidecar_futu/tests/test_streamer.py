"""Phase 7b.1 D1 - Futu quote streamer tests."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2 as pb
from sidecar_futu.streamer import (
    RET_OK,
    FutuStreamer,
    _SymbolEntry,
    canonical_to_futu_code,
)


def _sym(canonical_id: str) -> pb.SymbolRef:
    return pb.SymbolRef(canonical_id=canonical_id, raw_symbol=canonical_id)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.subscribe.return_value = (RET_OK, "")
    ctx.unsubscribe.return_value = (RET_OK, "")
    return ctx


def test_canonical_to_futu_code_mapping() -> None:
    assert canonical_to_futu_code("stock:0700:HK") == "HK.00700"
    assert canonical_to_futu_code("stock:700:HK") == "HK.00700"
    assert canonical_to_futu_code("idx:HSI:HK") == "HK.800000"
    assert canonical_to_futu_code("idx:HSCEI:HK") == "HK.800100"
    assert canonical_to_futu_code("idx:HHI:HK") == "HK.800200"
    assert canonical_to_futu_code("warrant:14841:HK") == "HK.14841"
    assert canonical_to_futu_code("cbbc:67890:HK") == "HK.67890"


@pytest.mark.asyncio
async def test_on_subscribe_first_ref_calls_openquote_subscribe() -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)

    await streamer.on_subscribe([_sym("stock:0700:HK")])

    ctx.subscribe.assert_called_once()
    args = ctx.subscribe.call_args.args
    assert args[0] == ["HK.00700"]


@pytest.mark.asyncio
async def test_on_subscribe_second_ref_increments_refcount_only() -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)

    await streamer.on_subscribe([_sym("stock:0700:HK")])
    await streamer.on_subscribe([_sym("stock:0700:HK")])

    ctx.subscribe.assert_called_once()
    assert streamer._upstream_refcount["stock:0700:HK"].refcount == 2


@pytest.mark.asyncio
async def test_on_unsubscribe_last_ref_calls_openquote_unsubscribe() -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)
    await streamer.on_subscribe([_sym("stock:0700:HK")])
    await streamer.on_subscribe([_sym("stock:0700:HK")])

    await streamer.on_unsubscribe([_sym("stock:0700:HK")])
    ctx.unsubscribe.assert_not_called()
    await streamer.on_unsubscribe([_sym("stock:0700:HK")])

    ctx.unsubscribe.assert_called_once()
    args = ctx.unsubscribe.call_args.args
    assert args[0] == ["HK.00700"]
    assert "stock:0700:HK" not in streamer._upstream_refcount


@pytest.mark.asyncio
async def test_on_resync_diffs_correctly() -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)
    streamer._upstream_refcount = {
        "stock:0700:HK": _SymbolEntry("HK.00700", 1),
        "idx:HSI:HK": _SymbolEntry("HK.800000", 1),
    }

    await streamer.on_resync([_sym("idx:HSI:HK"), _sym("warrant:14841:HK")])

    ctx.subscribe.assert_called_once()
    assert ctx.subscribe.call_args.args[0] == ["HK.14841"]
    ctx.unsubscribe.assert_called_once()
    assert ctx.unsubscribe.call_args.args[0] == ["HK.00700"]
    assert set(streamer._upstream_refcount) == {"idx:HSI:HK", "warrant:14841:HK"}


@pytest.mark.asyncio
async def test_tick_callback_fires_with_quote_message() -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)
    await streamer.start()
    received = MagicMock()
    streamer.tick_callback = received
    streamer._upstream_refcount = {
        "stock:0700:HK": _SymbolEntry("HK.00700", 1),
    }

    assert streamer._handler is not None
    streamer._handler.on_recv_rsp(
        {
            "code": "HK.00700",
            "last_price": "312.4",
            "bid_price": "312.2",
            "ask_price": "312.6",
            "volume": 123456,
            "data_date": "2026-05-04",
            "data_time": "15:30:00",
        }
    )
    await asyncio.sleep(0)

    received.assert_called_once()
    quote = received.call_args.args[0]
    assert isinstance(quote, pb.QuoteMessage)
    assert quote.canonical_id == "stock:0700:HK"
    assert quote.source == "futu"
    assert quote.last == "312.4"
    assert quote.bid == "312.2"
    assert quote.ask == "312.6"
    assert quote.volume == "123456"


@pytest.mark.asyncio
async def test_refcount_cap_skips_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx()
    streamer = FutuStreamer.for_tests(ctx)
    warning = MagicMock()
    monkeypatch.setattr("sidecar_futu.streamer._MAX_SYMBOLS", 3)
    monkeypatch.setattr("sidecar_futu.streamer.log.warning", warning)

    await streamer.on_subscribe(
        [
            _sym("stock:0001:HK"),
            _sym("stock:0002:HK"),
            _sym("stock:0003:HK"),
            _sym("stock:0004:HK"),
        ]
    )

    assert ctx.subscribe.call_count == 3
    assert "stock:0004:HK" not in streamer._upstream_refcount
    warning.assert_called_once()
    assert warning.call_args.args[0] == "futu.streamer.cap_hit"


@pytest.mark.skipif(
    os.environ.get("CI_USE_REAL_FUTU") != "1",
    reason="requires live FutuOpenD connection",
)
def test_real_futu_inclusion_gate() -> None:
    assert os.environ["CI_USE_REAL_FUTU"] == "1"
