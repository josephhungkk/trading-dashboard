"""Futu OpenQuoteContext quote streamer for HK market data."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

import structlog
from google.protobuf.timestamp_pb2 import Timestamp

from sidecar_futu import metrics
from sidecar_futu._generated.broker.v1 import broker_pb2 as pb

log = structlog.get_logger(module="sidecar_futu.streamer")

_MAX_SYMBOLS = 5000
RET_OK = 0
_INDEX_CODES = {
    "HSI": "HK.800000",
    "HSCEI": "HK.800100",
    "HHI": "HK.800200",
}


@dataclass(slots=True)
class _SymbolEntry:
    raw_futu_code: str
    refcount: int


class FutuStreamer:
    """Single OpenQuoteContext subscription manager with gRPC-driven refs."""

    def __init__(
        self,
        quote_ctx: Any,
        *,
        quote_handler_base: type[Any] | None = None,
        quote_subtype: Any | None = None,
        use_worker_thread: bool = True,
    ) -> None:
        self._quote_ctx = quote_ctx
        self._quote_handler_base = quote_handler_base
        self._quote_subtype_value = quote_subtype
        self._use_worker_thread = use_worker_thread
        self.tick_callback: Callable[[pb.QuoteMessage], None] | None = None
        self._handler: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs_lock = asyncio.Lock()
        self._upstream_refcount: dict[str, _SymbolEntry] = {}

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._handler = _build_quote_handler(
            self, self._quote_handler_base or _quote_handler_base()
        )
        await self._call_ctx(self._quote_ctx.set_handler, self._handler)

    async def stop(self) -> None:
        """Tear down the streamer — clears tick_callback and closes the
        underlying OpenQuoteContext. Idempotent: safe to call multiple
        times. Closes the context via worker thread to avoid blocking
        the event loop on Futu SDK socket cleanup.
        """
        self.tick_callback = None
        ctx = self._quote_ctx
        self._quote_ctx = None  # type: ignore[assignment]
        if ctx is None:
            return
        try:
            await self._call_ctx(ctx.close)
        except Exception as exc:
            log.warning("futu.streamer.close_error", error=str(exc))

    async def on_subscribe(self, symbols: list[pb.SymbolRef]) -> None:
        to_subscribe: list[tuple[str, str]] = []
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id = _canonical_id(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is not None:
                    entry.refcount += 1
                    continue
                if len(self._upstream_refcount) >= _MAX_SYMBOLS:
                    metrics.futu_streamer_subscribe_total.labels(
                        result="cap_hit"
                    ).inc()
                    log.warning(
                        "futu.streamer.cap_hit",
                        current=len(self._upstream_refcount),
                        cap=_MAX_SYMBOLS,
                        canonical_id=canonical_id,
                    )
                    continue
                raw_futu_code = canonical_to_futu_code(canonical_id)
                self._upstream_refcount[canonical_id] = _SymbolEntry(
                    raw_futu_code, 1
                )
                to_subscribe.append((canonical_id, raw_futu_code))
        for canonical_id, raw_futu_code in to_subscribe:
            await self._subscribe_one(canonical_id, raw_futu_code)

    async def on_unsubscribe(self, symbols: list[pb.SymbolRef]) -> None:
        to_unsubscribe: list[tuple[str, str]] = []
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id = _canonical_id(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is None:
                    continue
                entry.refcount -= 1
                if entry.refcount <= 0:
                    del self._upstream_refcount[canonical_id]
                    to_unsubscribe.append((canonical_id, entry.raw_futu_code))
        for canonical_id, raw_futu_code in to_unsubscribe:
            await self._unsubscribe_one(canonical_id, raw_futu_code)

    async def on_resync(self, expected: list[pb.SymbolRef]) -> None:
        async with self._subs_lock:
            expected_map = {
                _canonical_id(symbol): canonical_to_futu_code(_canonical_id(symbol))
                for symbol in expected
            }
            current_ids = set(self._upstream_refcount)
            expected_ids = set(expected_map)
            stale = [
                (key, self._upstream_refcount[key].raw_futu_code)
                for key in current_ids - expected_ids
            ]
            new = [(key, expected_map[key]) for key in expected_ids - current_ids]
            for canonical_id, _ in stale:
                del self._upstream_refcount[canonical_id]
            for canonical_id, raw_futu_code in new:
                self._upstream_refcount[canonical_id] = _SymbolEntry(
                    raw_futu_code, 1
                )
        for canonical_id, raw_futu_code in new:
            await self._subscribe_one(canonical_id, raw_futu_code)
        for canonical_id, raw_futu_code in stale:
            await self._unsubscribe_one(canonical_id, raw_futu_code)
        log.info(
            "futu.streamer.resync",
            expected=len(expected_map),
            subscribed=len(new),
            unsubscribed=len(stale),
        )

    async def _subscribe_one(self, canonical_id: str, raw_futu_code: str) -> None:
        try:
            ret, data = await self._call_ctx(
                self._quote_ctx.subscribe, [raw_futu_code], [self._quote_subtype()]
            )
            if ret != RET_OK:
                raise RuntimeError(str(data))
            metrics.futu_streamer_subscribe_total.labels(result="ok").inc()
            log.info(
                "futu.streamer.subscribe",
                canonical_id=canonical_id,
                raw_futu_code=raw_futu_code,
            )
        except Exception:
            metrics.futu_streamer_subscribe_total.labels(result="error").inc()
            async with self._subs_lock:
                self._upstream_refcount.pop(canonical_id, None)
            raise

    async def _unsubscribe_one(self, canonical_id: str, raw_futu_code: str) -> None:
        await self._call_ctx(
            self._quote_ctx.unsubscribe, [raw_futu_code], [self._quote_subtype()]
        )
        log.info(
            "futu.streamer.unsubscribe",
            canonical_id=canonical_id,
            raw_futu_code=raw_futu_code,
        )

    def _dispatch_quote_row(self, row: dict[str, Any]) -> None:
        canonical_id = self._canonical_for_raw(str(row.get("code") or ""))
        if not canonical_id:
            return
        message = _futu_quote_to_message(row, canonical_id)
        callback = self.tick_callback
        if callback is None:
            return
        try:
            callback(message)
        except Exception as exc:
            log.warning("futu.streamer.tick_callback_error", error=str(exc))
            return
        raw_futu_code = str(row.get("code") or "")
        metrics.futu_streamer_ticks_total.labels(symbol=raw_futu_code).inc()
        log.info(
            "futu.streamer.tick",
            canonical_id=canonical_id,
            raw_futu_code=raw_futu_code,
        )

    def _dispatch_quote_row_threadsafe(self, row: dict[str, Any]) -> None:
        # Capture the loop reference BEFORE checking — stop() may set
        # self._loop = None on another thread between the None-check and
        # call_soon_threadsafe(), and a closed loop also raises RuntimeError.
        loop = self._loop
        if loop is None:
            self._dispatch_quote_row(row)
            return
        try:
            loop.call_soon_threadsafe(self._dispatch_quote_row, row)
        except RuntimeError:
            # Loop closed mid-rotation — drop the tick rather than crash
            # the Futu SDK worker thread.
            log.warning("futu.streamer.dispatch_loop_closed")

    def _canonical_for_raw(self, raw_futu_code: str) -> str:
        for canonical_id, entry in self._upstream_refcount.items():
            if entry.raw_futu_code == raw_futu_code:
                return canonical_id
        return ""

    def _quote_subtype(self) -> Any:
        if self._quote_subtype_value is not None:
            return self._quote_subtype_value
        _prepare_futu_import()
        from futu import SubType

        return SubType.QUOTE

    async def _call_ctx(self, method: Callable[..., Any], *args: Any) -> Any:
        if not self._use_worker_thread:
            return method(*args)
        return await asyncio.to_thread(method, *args)

    @classmethod
    def for_tests(cls, mock_ctx: Any) -> Self:
        return cls(
            mock_ctx,
            quote_handler_base=_SyntheticQuoteHandlerBase,
            quote_subtype="QUOTE",
            use_worker_thread=False,
        )


class _SyntheticQuoteHandlerBase:
    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:
        return RET_OK, rsp_pb


def _build_quote_handler(streamer: FutuStreamer, base: type[Any]) -> Any:
    class _QuoteHandler(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__()
            self._streamer = streamer

        def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:
            if isinstance(rsp_pb, (dict, list)) or hasattr(rsp_pb, "iterrows"):
                ret, data = RET_OK, rsp_pb
            else:
                ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return ret, data
            for row in _iter_rows(data):
                self._streamer._dispatch_quote_row_threadsafe(row)
            return ret, data

    return _QuoteHandler()


def _quote_handler_base() -> type[Any]:
    _prepare_futu_import()
    from futu import StockQuoteHandlerBase

    return StockQuoteHandlerBase


def _prepare_futu_import() -> None:
    # Futu's SDK configures a file logger at import time. Keep that side effect
    # inside a writable temp home when the real home is unavailable in tests.
    futu_log_dir = Path.home() / ".com.futunn.FutuOpenD" / "Log"
    try:
        futu_log_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=futu_log_dir):
            pass
    except OSError:
        futu_import_home = Path(tempfile.gettempdir()) / "futu-home"
        futu_import_home.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(futu_import_home)
        os.environ["USERPROFILE"] = str(futu_import_home)


def canonical_to_futu_code(canonical_id: str) -> str:
    asset_type, symbol, region = canonical_id.split(":", 2)
    if region != "HK":
        raise ValueError(f"unsupported futu region: {region}")
    if asset_type == "stock":
        # Numeric HK stock codes pad to 5 digits (e.g. 700 -> HK.00700).
        # Non-numeric codes (rare HK GEM listings) pass through verbatim.
        try:
            return f"HK.{int(symbol):05d}"
        except ValueError:
            return f"HK.{symbol}"
    if asset_type == "idx" and symbol in _INDEX_CODES:
        return _INDEX_CODES[symbol]
    if asset_type in {"warrant", "cbbc"}:
        return f"HK.{symbol}"
    raise ValueError(f"unsupported futu canonical_id: {canonical_id}")


def _canonical_id(symbol: pb.SymbolRef) -> str:
    return symbol.canonical_id or symbol.raw_symbol


def _futu_quote_to_message(row: dict[str, Any], canonical_id: str) -> pb.QuoteMessage:
    received_at = Timestamp()
    received_at.FromDatetime(datetime.now(UTC))
    tick_time = _futu_row_timestamp(row)
    return pb.QuoteMessage(
        canonical_id=canonical_id,
        tick_time=tick_time,
        received_at=received_at,
        source="futu",
        last=_decimal_str(row.get("last_price")),
        bid=_decimal_str(row.get("bid_price")),
        ask=_decimal_str(row.get("ask_price")),
        volume=_int_str(row.get("volume")),
        day_high=_decimal_str(row.get("high_price")),
        day_low=_decimal_str(row.get("low_price")),
        open=_decimal_str(row.get("open_price")),
        prev_close=_decimal_str(row.get("prev_close_price")),
        change_pct=_decimal_str(row.get("change_rate")),
        change=_decimal_str(row.get("price_spread")),
        raw_payload=json.dumps(row, default=str, separators=(",", ":")).encode(),
    )


def _futu_row_timestamp(row: dict[str, Any]) -> Timestamp:
    ts = Timestamp()
    try:
        date = row.get("data_date")
        time_ = row.get("data_time")
        if date and time_:
            ts.FromDatetime(datetime.fromisoformat(f"{date} {time_}"))
            return ts
    except (TypeError, ValueError):
        pass
    return ts


def _iter_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if hasattr(data, "iterrows"):
        return [dict(row) for _, row in data.iterrows()]
    if isinstance(data, list):
        return [dict(row) for row in data]
    return []


def _decimal_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:
            return ""
        return str(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return ""


def _int_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:
            return ""
        return str(int(value))
    except (TypeError, ValueError):
        return ""
