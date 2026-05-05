"""IBKR market-data streamer for Phase 7b.1 quotes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
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
                    # Bookkeeping must complete atomically with reqMktData
                    # — if any dict write raises after the IBKR side
                    # accepted the request, roll back via cancelMktData so
                    # we don't leak a permanently-orphaned market-data
                    # subscription slot (per-gateway L1 cap is 100).
                    self._upstream_refcount[canonical_id] = _SymbolEntry(req_id, 1)
                    self.reqId_to_canonical[req_id] = canonical_id
                    self._contracts_by_req_id[req_id] = contract
                except Exception:
                    metrics.ibkr_streamer_subscribe_total.labels(result="error").inc()
                    log.warning(
                        "ibkr.streamer.subscribe",
                        canonical_id=canonical_id,
                        result="error",
                        exc_info=True,
                    )
                    if "contract" in locals() and "ticker" in locals():
                        with contextlib.suppress(Exception):
                            self._ib.cancelMktData(contract)
                    raise
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
            # cancelMktData can raise if the gateway already closed the
            # reqId (e.g. mid-rotation reset). State has already been
            # cleaned up under the lock; treat the cancel as best-effort.
            try:
                self._ib.cancelMktData(contract)
            except Exception as exc:
                log.warning(
                    "ibkr.streamer.cancel_failed",
                    canonical_id=canonical_id,
                    req_id=req_id,
                    error=str(exc),
                )
                continue
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
        # Wrap the entire body in a broad except so a malformed Ticker
        # row or unexpected protobuf shape doesn't propagate out — under
        # ib_async's eventkit, an unhandled exception in a listener
        # detaches the listener, silently halting all future ticks.
        try:
            for ticker in _iter_tickers(tickers):
                self._dispatch_one_ticker(ticker)
        except Exception:
            log.exception("ibkr.streamer.dispatch_unhandled")

    def _dispatch_one_ticker(self, ticker: Any) -> None:
        quote = self._ticker_to_quote(ticker)
        if quote is None:
            return
        callback = self.tick_callback
        if callback is None:
            return
        try:
            callback(quote)
        except Exception as exc:
            log.warning("ibkr.streamer.tick_callback_error", error=str(exc))
            return
        # Metric + tick log only on successful delivery — counting
        # before-callback was misleading observability (the previous
        # implementation logged a tick even when the callback raised).
        metrics.ibkr_streamer_ticks_total.labels(symbol=quote.canonical_id).inc()
        log.info("ibkr.streamer.tick", canonical_id=quote.canonical_id)

    def _ticker_to_quote(self, ticker: Any) -> pb.QuoteMessage | None:
        req_id = _ticker_req_id(self._ib, ticker)
        canonical_id = self.reqId_to_canonical.get(req_id)
        if canonical_id is None:
            return None
        contract = getattr(ticker, "contract", None)
        currency = str(getattr(contract, "currency", "")).upper()
        last = _decimal_or_none(getattr(ticker, "last", None))
        bid = _decimal_or_none(getattr(ticker, "bid", None))
        ask = _decimal_or_none(getattr(ticker, "ask", None))
        # Pence-quoted feed signal: the canonical_id's country is UK (the
        # region from canonical_id parts) OR IBKR explicitly stamped the
        # currency as GBX. Using ticker.contract.exchange for this check
        # would be defeated by SMART-routing (which reports "SMART", not
        # "LSE"); the canonical_id country is the durable signal.
        _, _, region, _ = _canonical_parts(canonical_id)
        is_pence = region == "UK" or currency == "GBX"
        normalized = (
            _normalize_gbx(last, is_pence=is_pence),
            _normalize_gbx(bid, is_pence=is_pence),
            _normalize_gbx(ask, is_pence=is_pence),
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
    *,
    is_pence: bool,
) -> Decimal | None:
    """Convert pence -> pounds when ``is_pence`` is True.

    The caller derives ``is_pence`` from the canonical_id's country (UK)
    and/or IBKR's contract.currency (GBX). A price-threshold heuristic
    (e.g. ``< 100``) is NOT used — IBKR consistently quotes LSE-listed
    GBP equities in pence regardless of price level, and a threshold
    check would silently corrupt genuine sub-£1 stocks (e.g. Lloyds at
    90 GBP would become 0.9 GBP). Negative prices are IBKR sentinels
    for missing data — pass through so downstream can filter.
    """
    if price is None or price < 0:
        return price
    if not is_pence:
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
    # Explicit ROUND_HALF_EVEN — without it quantize() inherits whatever
    # rounding mode the active decimal context has, which ib_async or
    # third-party libs may have changed. Banker's rounding is the
    # standard choice for tick prices.
    return str(value.quantize(_PRICE_QUANT, rounding=ROUND_HALF_EVEN))


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
