"""Phase 7b.1 E1 - IBKR quote streamer tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sidecar_ibkr._generated.broker.v1 import broker_pb2 as pb
from sidecar_ibkr.streamer import (
    IBKRStreamer,
    _normalize_gbx,
    _SymbolEntry,
    canonical_to_contract,
)


class _Event:
    def __init__(self) -> None:
        self._callbacks: list[Callable[[object], None]] = []

    def __iadd__(self, callback: Callable[[object], None]) -> _Event:
        self._callbacks.append(callback)
        return self

    def __isub__(self, callback: Callable[[object], None]) -> _Event:
        self._callbacks.remove(callback)
        return self

    def emit(self, payload: object) -> None:
        for callback in tuple(self._callbacks):
            callback(payload)


class _FakeIB:
    def __init__(self) -> None:
        self.pendingTickersEvent = _Event()
        self.reqMktData = MagicMock(side_effect=self._req_mkt_data)
        self.cancelMktData = MagicMock()
        self._next_req_id = 1

    def _req_mkt_data(
        self,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
    ) -> object:
        del generic_tick_list, snapshot, regulatory_snapshot
        req_id = self._next_req_id
        self._next_req_id += 1
        return SimpleNamespace(tickerId=req_id, contract=contract)


def _sym(canonical_id: str, *, exchange: str = "") -> pb.SymbolRef:
    return pb.SymbolRef(
        canonical_id=canonical_id,
        raw_symbol=canonical_id,
        exchange=exchange,
    )


def test_normalize_gbx_lse_gbp_below_100_divides_by_100() -> None:
    assert _normalize_gbx(Decimal("70.45"), "LSE", "GBP") == Decimal("0.7045")


def test_normalize_gbx_lse_gbp_above_100_passes_through() -> None:
    assert _normalize_gbx(Decimal("250.00"), "LSE", "GBP") == Decimal("250.00")


def test_normalize_gbx_non_lse_passes_through() -> None:
    assert _normalize_gbx(Decimal("213.45"), "NASDAQ", "USD") == Decimal("213.45")


def test_normalize_gbx_lse_zero_price_returns_zero() -> None:
    assert _normalize_gbx(Decimal("0"), "LSE", "GBP") == Decimal("0")


def test_normalize_gbx_none_returns_none() -> None:
    assert _normalize_gbx(None, "LSE", "GBP") is None


def test_canonical_to_contract_us_stock() -> None:
    contract = canonical_to_contract(_sym("stock:AAPL:US"))
    assert contract.symbol == "AAPL"
    assert contract.exchange == "SMART"
    assert contract.primaryExchange == "NASDAQ"
    assert contract.secType == "STK"
    assert contract.currency == "USD"


def test_canonical_to_contract_uk_stock() -> None:
    contract = canonical_to_contract(_sym("stock:VOD:UK"))
    assert contract.symbol == "VOD"
    assert contract.exchange == "LSE"
    assert contract.secType == "STK"
    assert contract.currency == "GBP"


def test_canonical_to_contract_us_index_spx() -> None:
    contract = canonical_to_contract(_sym("idx:SPX:US"))
    assert contract.symbol == "SPX"
    assert contract.exchange == "CBOE"
    assert contract.secType == "IND"
    assert contract.currency == "USD"


def test_canonical_to_contract_unknown_raises() -> None:
    with pytest.raises(ValueError):
        canonical_to_contract(_sym("stock:XXXX:UNKNOWN"))


@pytest.mark.asyncio
async def test_on_subscribe_first_ref_calls_reqMktData() -> None:  # noqa: N802
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)

    await streamer.on_subscribe([_sym("stock:AAPL:US")])

    ib.reqMktData.assert_called_once()
    assert streamer._upstream_refcount["stock:AAPL:US"] == _SymbolEntry(1, 1)
    assert streamer.reqId_to_canonical[1] == "stock:AAPL:US"


@pytest.mark.asyncio
async def test_on_subscribe_second_ref_increments_refcount_only() -> None:
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)

    await streamer.on_subscribe([_sym("stock:AAPL:US")])
    await streamer.on_subscribe([_sym("stock:AAPL:US")])

    ib.reqMktData.assert_called_once()
    assert streamer._upstream_refcount["stock:AAPL:US"].refcount == 2


@pytest.mark.asyncio
async def test_on_unsubscribe_last_ref_calls_cancelMktData() -> None:  # noqa: N802
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)
    await streamer.on_subscribe([_sym("stock:AAPL:US")])
    await streamer.on_subscribe([_sym("stock:AAPL:US")])

    await streamer.on_unsubscribe([_sym("stock:AAPL:US")])
    ib.cancelMktData.assert_not_called()
    await streamer.on_unsubscribe([_sym("stock:AAPL:US")])

    ib.cancelMktData.assert_called_once()
    assert "stock:AAPL:US" not in streamer._upstream_refcount
    assert 1 not in streamer.reqId_to_canonical


@pytest.mark.asyncio
async def test_on_resync_diffs_correctly() -> None:
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)
    await streamer.on_subscribe([_sym("stock:AAPL:US")])
    await streamer.on_subscribe([_sym("idx:SPX:US")])

    await streamer.on_resync([_sym("idx:SPX:US"), _sym("stock:MSFT:US")])

    assert ib.reqMktData.call_count == 3
    ib.cancelMktData.assert_called_once()
    assert set(streamer._upstream_refcount) == {"idx:SPX:US", "stock:MSFT:US"}


@pytest.mark.asyncio
async def test_reqid_pool_cap_at_100() -> None:
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)

    await streamer.on_subscribe(
        [_sym(f"stock:SYM{i}:US") for i in range(101)]
    )

    assert ib.reqMktData.call_count == 100
    assert len(streamer._upstream_refcount) == 100
    assert "stock:SYM100:US" not in streamer._upstream_refcount


@pytest.mark.asyncio
async def test_pending_ticker_event_fires_tick_callback() -> None:
    ib = _FakeIB()
    streamer = IBKRStreamer.for_tests(ib)
    await streamer.start()
    received = MagicMock()
    streamer.tick_callback = received
    await streamer.on_subscribe([_sym("stock:VOD:UK")])
    contract = ib.reqMktData.call_args.args[0]
    ticker = SimpleNamespace(
        tickerId=1,
        contract=contract,
        last=70.45,
        bid=70.40,
        ask=70.50,
        volume=123456,
        high=72,
        low=69,
        open=70,
        close=71,
        time=None,
        lastTimestamp=None,
    )

    ib.pendingTickersEvent.emit([ticker])

    received.assert_called_once()
    quote = received.call_args.args[0]
    assert isinstance(quote, pb.QuoteMessage)
    assert quote.canonical_id == "stock:VOD:UK"
    assert quote.source == "ibkr"
    assert quote.last == "0.7045"
    assert quote.bid == "0.7040"
    assert quote.ask == "0.7050"
    assert quote.volume == "123456"
