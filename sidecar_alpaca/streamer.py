"""Alpaca IEX websocket streamer for streaming quote RPCs."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Self

import structlog
import websockets
from google.protobuf.timestamp_pb2 import Timestamp
from websockets.exceptions import ConnectionClosed

from sidecar_alpaca import config
from sidecar_alpaca._generated.broker.v1 import broker_pb2 as pb
from sidecar_alpaca.auth import AuthCache
from sidecar_alpaca.metrics import (
    ALPACA_QUOTE_TICKS_TOTAL,
    ALPACA_SUBSCRIPTION_ACTIVE,
    ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL,
    ALPACA_WS_RECONNECT_TOTAL,
)

log = structlog.get_logger(module="sidecar_alpaca.streamer")

_IEX_CAP = 30
_BACKOFF_MAX_SEC = 30.0
_AUTH_TIMEOUT_SEC = 10.0


class _WebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


class AlpacaStreamer:
    """Per-RPC Alpaca streamer with isolated endpoint tasks."""

    def __init__(self, auth_cache: AuthCache) -> None:
        self._auth = auth_cache
        self.tick_callback: Callable[[pb.QuoteMessage], Awaitable[None]] | None = None
        self._iex_ws: _WebSocket | None = None
        self._iex_active: set[str] = set()
        self._iex_symbol_map: dict[str, str] = {}
        self._supervisor_task: asyncio.Task[None] | None = None
        self._iex_task: asyncio.Task[None] | None = None
        self._crypto_task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._subs_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._shutting_down = False
        self._iex_restart_requested = False

    async def start(self) -> None:
        async with self._task_lock:
            if self._supervisor_task is not None:
                return
            self._shutting_down = False
            self._supervisor_task = asyncio.create_task(
                self._supervisor_loop(), name="alpaca-streamer-supervisor"
            )

    async def stop(self) -> None:
        self._shutting_down = True
        tasks = [
            task
            for task in (self._supervisor_task, self._iex_task, self._crypto_task)
            if task is not None
        ] + list(self._callback_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor_task = None
        self._iex_task = None
        self._crypto_task = None
        self._callback_tasks.clear()
        await self._close_iex_ws()
        self._shutting_down = False

    async def on_subscribe(self, symbols: list[pb.SymbolRef] | list[str]) -> None:
        to_subscribe: list[str] = []
        async with self._subs_lock:
            for raw_symbol, canonical_id in _symbol_pairs(symbols):
                if raw_symbol in self._iex_active:
                    continue
                if len(self._iex_active) >= _IEX_CAP:
                    ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
                        endpoint="iex", reason="cap_exceeded"
                    ).inc()
                    log.warning(
                        "alpaca.streamer.iex_cap_exceeded",
                        cap=_IEX_CAP,
                        symbol=raw_symbol,
                    )
                    continue
                self._iex_active.add(raw_symbol)
                self._iex_symbol_map[raw_symbol] = canonical_id
                to_subscribe.append(raw_symbol)
            self._record_active_locked()
        if to_subscribe:
            await self._send_ws_subscribe(sorted(to_subscribe))
            await self._restart_iex_loop("subscribe_replay")

    async def on_unsubscribe(self, symbols: list[pb.SymbolRef] | list[str]) -> None:
        to_unsubscribe: list[str] = []
        async with self._subs_lock:
            for raw_symbol, _canonical_id in _symbol_pairs(symbols):
                if raw_symbol not in self._iex_active:
                    continue
                self._iex_active.remove(raw_symbol)
                self._iex_symbol_map.pop(raw_symbol, None)
                to_unsubscribe.append(raw_symbol)
            self._record_active_locked()
        if to_unsubscribe:
            await self._send_ws_unsubscribe(sorted(to_unsubscribe))

    async def on_resync(self, symbols: list[pb.SymbolRef] | list[str]) -> None:
        expected_pairs = _symbol_pairs(symbols)
        expected = {raw_symbol for raw_symbol, _canonical_id in expected_pairs}
        async with self._subs_lock:
            current = set(self._iex_active)
            stale = current - expected
            new = expected - current
            self._iex_active = set(expected)
            self._iex_symbol_map = {
                raw_symbol: canonical_id
                for raw_symbol, canonical_id in expected_pairs
                if raw_symbol in self._iex_active
            }
            self._record_active_locked()
        if new:
            await self._send_ws_subscribe(sorted(new))
        if stale:
            await self._send_ws_unsubscribe(sorted(stale))

    async def _supervisor_loop(self) -> None:
        self._iex_task = asyncio.create_task(self._iex_loop(), name="alpaca-iex-loop")
        self._crypto_task = asyncio.create_task(
            self._crypto_loop(), name="alpaca-crypto-loop"
        )
        while not self._shutting_down:
            tasks = {self._iex_task, self._crypto_task} - {None}
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if self._shutting_down:
                return
            for task in done:
                await self._restart_done_task(task)

    async def _restart_done_task(self, task: asyncio.Task[None]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                log.warning("alpaca.streamer.endpoint_crash", error=str(exc))
        if task is self._iex_task or task.get_name() == "alpaca-iex-loop":
            if self._iex_restart_requested:
                self._iex_restart_requested = False
            else:
                ALPACA_WS_RECONNECT_TOTAL.labels(
                    endpoint="iex", reason="loop_crash"
                ).inc()
            self._iex_task = asyncio.create_task(
                self._iex_loop(), name="alpaca-iex-loop"
            )
            return
        self._crypto_task = asyncio.create_task(
            self._crypto_loop(), name="alpaca-crypto-loop"
        )

    async def _iex_loop(self) -> None:
        backoff_attempt = 0
        while not self._shutting_down:
            try:
                await self._connect_iex_once()
                backoff_attempt = 0
            except (ConnectionClosed, OSError) as exc:
                if self._shutting_down:
                    return
                ALPACA_WS_RECONNECT_TOTAL.labels(
                    endpoint="iex", reason="ws_disconnect"
                ).inc()
                log.warning("alpaca.streamer.iex_disconnect", error=str(exc))
                await asyncio.sleep(min(2**backoff_attempt, _BACKOFF_MAX_SEC))
                backoff_attempt += 1

    async def _connect_iex_once(self) -> None:
        api_key, api_secret = await self._auth.get_credentials()
        async with websockets.connect(
            config.BASE_URL_DATA,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._iex_ws = ws
            try:
                await self._authenticate(ws, api_key, api_secret)
                await self._replay_iex_subscriptions()
                while not self._shutting_down:
                    self._dispatch_frame(await ws.recv())
            finally:
                if self._iex_ws is ws:
                    self._iex_ws = None

    async def _authenticate(
        self, ws: _WebSocket, api_key: str, api_secret: str
    ) -> None:
        await ws.send(
            json.dumps({"action": "auth", "key": api_key, "secret": api_secret})
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=_AUTH_TIMEOUT_SEC)
        if not _is_auth_success(_load_frame(raw)):
            ALPACA_WS_RECONNECT_TOTAL.labels(
                endpoint="iex", reason="auth_rejected"
            ).inc()
            raise RuntimeError("alpaca IEX auth rejected")
        log.info("alpaca.streamer.iex_auth_ok")

    async def _replay_iex_subscriptions(self) -> None:
        async with self._subs_lock:
            active = sorted(self._iex_active)
        if active:
            await self._send_ws_subscribe(active)

    async def _send_ws_subscribe(self, symbols: list[str]) -> None:
        if self._iex_ws is None or not symbols:
            return
        await self._iex_ws.send(
            json.dumps(
                {"action": "subscribe", "trades": [], "quotes": symbols, "bars": []}
            )
        )
        log.info("alpaca.streamer.iex_subscribe", symbols=symbols)

    async def _send_ws_unsubscribe(self, symbols: list[str]) -> None:
        if self._iex_ws is None or not symbols:
            return
        await self._iex_ws.send(
            json.dumps(
                {"action": "unsubscribe", "trades": [], "quotes": symbols, "bars": []}
            )
        )
        log.info("alpaca.streamer.iex_unsubscribe", symbols=symbols)

    async def _restart_iex_loop(self, reason: str) -> None:
        ALPACA_WS_RECONNECT_TOTAL.labels(endpoint="iex", reason=reason).inc()
        task = self._iex_task
        if task is None or task.done():
            return
        self._iex_restart_requested = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _crypto_loop(self) -> None:
        await asyncio.Event().wait()

    def _dispatch_frame(self, raw: str | bytes) -> None:
        for item in _load_frame(raw):
            msg_type = item.get("T")
            if msg_type == "q":
                self._dispatch_quote(item)
                continue
            if msg_type == "error":
                self._handle_error(item)

    def _dispatch_quote(self, row: dict[str, Any]) -> None:
        quote = self._row_to_quote(row)
        if quote is None or self.tick_callback is None:
            return
        callback = self.tick_callback
        task = asyncio.create_task(
            self._invoke_tick_callback(callback, quote),
            name="alpaca-quote-callback",
        )
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)
        ALPACA_QUOTE_TICKS_TOTAL.labels(endpoint="iex", mode=config.MODE).inc()

    async def _invoke_tick_callback(
        self,
        callback: Callable[[pb.QuoteMessage], Awaitable[None]],
        quote: pb.QuoteMessage,
    ) -> None:
        try:
            await callback(quote)
        except Exception as exc:
            log.warning("alpaca.streamer.tick_callback_error", exc_info=exc)

    def _row_to_quote(self, row: dict[str, Any]) -> pb.QuoteMessage | None:
        raw_symbol = str(row.get("S") or "")
        if not raw_symbol:
            return None
        received_at = Timestamp()
        received_at.FromDatetime(datetime.now(UTC))
        tick_time = Timestamp()
        _set_tick_time(tick_time, row.get("t"))
        return pb.QuoteMessage(
            canonical_id=self._iex_symbol_map.get(raw_symbol, raw_symbol),
            tick_time=tick_time,
            received_at=received_at,
            source="alpaca",
            bid=_decimal_str(row.get("bp")),
            ask=_decimal_str(row.get("ap")),
            raw_payload=json.dumps(row, separators=(",", ":")).encode(),
        )

    def _handle_error(self, row: dict[str, Any]) -> None:
        code = row.get("code")
        if code == 410:
            log.warning("alpaca.streamer.iex_symbol_not_subscribed", msg=row.get("msg"))
            return
        log.warning("alpaca.streamer.iex_error", code=code, msg=row.get("msg"))

    async def _close_iex_ws(self) -> None:
        ws = self._iex_ws
        self._iex_ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _record_active_locked(self) -> None:
        ALPACA_SUBSCRIPTION_ACTIVE.labels(endpoint="iex", mode=config.MODE).set(
            len(self._iex_active)
        )

    @classmethod
    def for_tests(cls) -> Self:
        auth = AuthCache()
        streamer = cls(auth)
        return streamer


def _symbol_pairs(symbols: list[pb.SymbolRef] | list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for symbol in symbols:
        if isinstance(symbol, str):
            pairs.append((symbol, symbol))
            continue
        raw_symbol = symbol.raw_symbol or symbol.canonical_id
        canonical_id = symbol.canonical_id or raw_symbol
        if raw_symbol:
            pairs.append((raw_symbol, canonical_id))
    return pairs


def _load_frame(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    frame = json.loads(raw)
    if isinstance(frame, list):
        return [item for item in frame if isinstance(item, dict)]
    if isinstance(frame, dict):
        return [frame]
    return []


def _is_auth_success(frame: list[dict[str, Any]]) -> bool:
    return any(
        item.get("T") == "success" and item.get("msg") == "authenticated"
        for item in frame
    )


def _set_tick_time(timestamp: Timestamp, value: Any) -> None:
    if not value:
        return
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        log.warning("alpaca.streamer.bad_tick_time", value=value, exc_info=exc)
        return
    timestamp.FromDatetime(parsed)


def _decimal_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:
            return ""
        return str(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        log.warning("alpaca.streamer.bad_decimal", value=value, exc_info=exc)
        return ""
