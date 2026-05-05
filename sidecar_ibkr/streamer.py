"""IBKR market-data streamer for Phase 7b.1 quotes."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self

import structlog
from google.protobuf.timestamp_pb2 import Timestamp
from ib_async import Contract  # type: ignore[import-untyped]

from sidecar_ibkr import metrics
from sidecar_ibkr._generated.broker.v1 import broker_pb2 as pb

log = structlog.get_logger(module="sidecar_ibkr.streamer")

_MAX_SYMBOLS = 100
_PRICE_QUANT = Decimal("0.0001")
_US_INDEX_EXCHANGE = "CBOE"
_INDEX_CONTRACTS = {
    "idx:SPX:US": ("SPX", _US_INDEX_EXCHANGE, "USD"),
    "idx:VIX:US": ("VIX", _US_INDEX_EXCHANGE, "USD"),
    "idx:NDX:US": ("NDX", _US_INDEX_EXCHANGE, "USD"),
    "idx:COMPX:US": ("COMPX", _US_INDEX_EXCHANGE, "USD"),
    "idx:DJI:US": ("DJI", _US_INDEX_EXCHANGE, "USD"),
    "idx:RUT:US": ("RUT", _US_INDEX_EXCHANGE, "USD"),
    "idx:DAX:DE": ("DAX", "EUREX", "EUR"),
}


@dataclass(slots=True)
class _SymbolEntry:
    req_id: int
    refcount: int


class IBKRStreamer:
    """Single IBKR market-data subscription manager with gRPC-driven refs."""

    def __init__(self, ib: Any) -> None:
        self._ib = ib
        self.tick_callback: Callable[[pb.QuoteMessage], None] | None = None
        self._subs_lock = asyncio.Lock()
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._upstream_refcount: dict[str, _SymbolEntry] = {}
        self.reqId_to_canonical: dict[int, str] = {}
        self._contracts_by_req_id: dict[int, Contract] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._ib.pendingTickersEvent += self._on_pending_tickers
        self._started = True

    async def stop(self) -> None:
        self.tick_callback = None
        if self._started:
            self._ib.pendingTickersEvent -= self._on_pending_tickers
        self._started = False
        self._loop = None

    async def on_subscribe(self, symbols: list[pb.SymbolRef]) -> None:
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id = _canonical_id(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is not None:
                    entry.refcount += 1
                    continue
                if len(self._upstream_refcount) >= _MAX_SYMBOLS:
                    metrics.ibkr_streamer_subscribe_total.labels(
                        result="cap_hit"
                    ).inc()
                    metrics.quote_ibkr_subs_cap_hit_total.inc()
                    log.warning(
                        "ibkr.streamer.cap_hit",
                        current=len(self._upstream_refcount),
                        cap=_MAX_SYMBOLS,
                        canonical_id=canonical_id,
                    )
                    continue
                try:
                    contract = canonical_to_contract(symbol)
                    ticker = self._ib.reqMktData(contract, "", False, False)
                    req_id = _ticker_req_id(self._ib, ticker)
                except Exception:
                    metrics.ibkr_streamer_subscribe_total.labels(result="error").inc()
                    log.warning(
                        "ibkr.streamer.subscribe",
                        canonical_id=canonical_id,
                        result="error",
                        exc_info=True,
                    )
                    raise
                self._upstream_refcount[canonical_id] = _SymbolEntry(req_id, 1)
                self.reqId_to_canonical[req_id] = canonical_id
                self._contracts_by_req_id[req_id] = contract
                metrics.ibkr_streamer_subscribe_total.labels(result="ok").inc()
                log.info(
                    "ibkr.streamer.subscribe",
                    canonical_id=canonical_id,
                    req_id=req_id,
                )

    async def on_unsubscribe(self, symbols: list[pb.SymbolRef]) -> None:
        to_cancel: list[tuple[str, int, Contract]] = []
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id = _canonical_id(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is None:
                    continue
                entry.refcount -= 1
                if entry.refcount > 0:
                    continue
                contract = self._contracts_by_req_id.pop(entry.req_id, None)
                if contract is not None:
                    to_cancel.append((canonical_id, entry.req_id, contract))
                del self._upstream_refcount[canonical_id]
                self.reqId_to_canonical.pop(entry.req_id, None)
        for canonical_id, req_id, contract in to_cancel:
            self._ib.cancelMktData(contract)
            log.info(
                "ibkr.streamer.unsubscribe",
                canonical_id=canonical_id,
                req_id=req_id,
            )

    async def on_resync(self, expected: list[pb.SymbolRef]) -> None:
        expected_ids = {_canonical_id(symbol) for symbol in expected}
        async with self._subs_lock:
            current_ids = set(self._upstream_refcount)
        stale = [
            pb.SymbolRef(canonical_id=canonical_id)
            for canonical_id in sorted(current_ids - expected_ids)
        ]
        new = [
            symbol
            for symbol in expected
            if _canonical_id(symbol) not in current_ids
        ]
        if new:
            await self.on_subscribe(new)
        if stale:
            await self.on_unsubscribe(stale)
        log.info(
            "ibkr.streamer.resync",
            expected=len(expected_ids),
            subscribed=len(new),
            unsubscribed=len(stale),
        )

    def _on_pending_tickers(self, tickers: object) -> None:
        loop = self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if loop is None or loop == running_loop:
            self._dispatch_pending_tickers(tickers)
            return
        try:
            loop.call_soon_threadsafe(self._dispatch_pending_tickers, tickers)
        except RuntimeError:
            log.warning("ibkr.streamer.dispatch_loop_closed")

    def _dispatch_pending_tickers(self, tickers: object) -> None:
        for ticker in _iter_tickers(tickers):
            quote = self._ticker_to_quote(ticker)
            if quote is None:
                continue
            callback = self.tick_callback
            if callback is None:
                continue
            try:
                callback(quote)
            except Exception as exc:
                log.warning("ibkr.streamer.tick_callback_error", error=str(exc))
                continue
            metrics.ibkr_streamer_ticks_total.labels(symbol=quote.canonical_id).inc()
            log.info("ibkr.streamer.tick", canonical_id=quote.canonical_id)

    def _ticker_to_quote(self, ticker: Any) -> pb.QuoteMessage | None:
        req_id = _ticker_req_id(self._ib, ticker)
        canonical_id = self.reqId_to_canonical.get(req_id)
        if canonical_id is None:
            return None
        contract = getattr(ticker, "contract", None)
        exchange = str(getattr(contract, "exchange", ""))
        currency = str(getattr(contract, "currency", ""))
        last = _decimal_or_none(getattr(ticker, "last", None))
        bid = _decimal_or_none(getattr(ticker, "bid", None))
        ask = _decimal_or_none(getattr(ticker, "ask", None))
        normalized = (
            _normalize_gbx(last, exchange, currency),
            _normalize_gbx(bid, exchange, currency),
            _normalize_gbx(ask, exchange, currency),
        )
        if normalized != (last, bid, ask):
            metrics.quote_uk_pence_normalizations_total.inc()
            log.info("ibkr.streamer.gbx_normalized", canonical_id=canonical_id)
        received_at = Timestamp()
        received_at.FromDatetime(datetime.now(UTC))
        tick_time = Timestamp()
        tick_dt = getattr(ticker, "time", None) or getattr(ticker, "lastTimestamp", None)
        if isinstance(tick_dt, datetime):
            tick_time.FromDatetime(tick_dt)
        return pb.QuoteMessage(
            canonical_id=canonical_id,
            tick_time=tick_time,
            received_at=received_at,
            source="ibkr",
            last=_decimal_str(normalized[0]),
            bid=_decimal_str(normalized[1]),
            ask=_decimal_str(normalized[2]),
            volume=_int_str(getattr(ticker, "volume", None)),
            day_high=_decimal_str(_decimal_or_none(getattr(ticker, "high", None))),
            day_low=_decimal_str(_decimal_or_none(getattr(ticker, "low", None))),
            open=_decimal_str(_decimal_or_none(getattr(ticker, "open", None))),
            prev_close=_decimal_str(_decimal_or_none(getattr(ticker, "close", None))),
            raw_payload=json.dumps(_ticker_payload(ticker), default=str).encode(),
        )

    @classmethod
    def for_tests(cls, ib: Any) -> Self:
        return cls(ib)


def canonical_to_contract(symbol: pb.SymbolRef) -> Contract:
    canonical_id = _canonical_id(symbol)
    asset_type, raw_symbol, region, override_exchange = _canonical_parts(canonical_id)
    base_canonical_id = f"{asset_type}:{raw_symbol}:{region}"
    exchange = override_exchange or symbol.exchange
    if asset_type == "stock" and region == "US":
        primary = exchange or "NASDAQ"
        if primary not in {"NASDAQ", "NYSE"}:
            primary = "NASDAQ"
        return Contract(
            symbol=raw_symbol,
            secType="STK",
            exchange="SMART",
            primaryExchange=primary,
            currency="USD",
        )
    if asset_type == "stock" and region == "UK":
        return Contract(
            symbol=raw_symbol,
            secType="STK",
            exchange=exchange or "LSE",
            currency="GBP",
        )
    if base_canonical_id in _INDEX_CONTRACTS:
        symbol_name, index_exchange, currency = _INDEX_CONTRACTS[base_canonical_id]
        return Contract(
            symbol=symbol_name,
            secType="IND",
            exchange=override_exchange or exchange or index_exchange,
            currency=currency,
        )
    raise ValueError(f"unsupported ibkr canonical_id: {canonical_id}")


def _normalize_gbx(
    price: Decimal | None,
    exchange: str,
    currency: str,
) -> Decimal | None:
    if price is None:
        return None
    if exchange != "LSE" or currency != "GBP":
        return price
    if price >= Decimal("100"):
        return price
    return price / Decimal("100")


def _canonical_id(symbol: pb.SymbolRef) -> str:
    return symbol.canonical_id or symbol.raw_symbol


def _canonical_parts(canonical_id: str) -> tuple[str, str, str, str]:
    parts = canonical_id.split(":")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], ""
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    raise ValueError(f"unsupported ibkr canonical_id: {canonical_id}")


def _ticker_req_id(ib: Any, ticker: Any) -> int:
    explicit = getattr(ticker, "tickerId", None) or getattr(ticker, "reqId", None)
    if explicit is not None:
        return int(explicit)
    wrapper = getattr(ib, "wrapper", None)
    ticker2req = getattr(wrapper, "ticker2ReqId", {})
    try:
        return int(ticker2req["mktData"][ticker])
    except (KeyError, TypeError):
        return 0


def _iter_tickers(tickers: object) -> list[Any]:
    if isinstance(tickers, (list, set, tuple)):
        return list(tickers)
    return [tickers]


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_str(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.quantize(_PRICE_QUANT))


def _int_str(value: object) -> str:
    decimal_value = _decimal_or_none(value)
    if decimal_value is None:
        return ""
    return str(int(decimal_value))


def _ticker_payload(ticker: Any) -> dict[str, object]:
    return {
        "last": getattr(ticker, "last", None),
        "bid": getattr(ticker, "bid", None),
        "ask": getattr(ticker, "ask", None),
        "volume": getattr(ticker, "volume", None),
        "high": getattr(ticker, "high", None),
        "low": getattr(ticker, "low", None),
        "open": getattr(ticker, "open", None),
        "close": getattr(ticker, "close", None),
    }
