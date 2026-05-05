"""Alpaca websocket streamer for streaming quote RPCs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Self

import structlog
import websockets
from google.protobuf.timestamp_pb2 import Timestamp
from websockets.exceptions import ConnectionClosed

from sidecar_alpaca import config, normalize
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
_CRYPTO_CAP = 30
_BACKOFF_MAX_SEC = 30.0
_AUTH_TIMEOUT_SEC = 10.0
_SUBSCRIBE_REJECTION_CODES = {
    409: "cap_exceeded",
    410: "cap_exceeded",
    401: "entitlement",
    405: "entitlement",
}
_DRIFT_SENTINELS = {
    "cap_exceeded": b'{"drift":"cap_exceeded"}',
    "entitlement": b'{"drift":"entitlement"}',
    "unknown": b'{"drift":"unknown"}',
}


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
        self._crypto_ws: _WebSocket | None = None
        self._iex_active: set[str] = set()
        self._crypto_active: set[str] = set()
        self._iex_symbol_map: dict[str, str] = {}
        self._iex_pending_subs: deque[str] = deque()
        self._crypto_pending_subs: deque[str] = deque()
        self._supervisor_task: asyncio.Task[None] | None = None
        self._iex_supervisor_task: asyncio.Task[None] | None = None
        self._crypto_supervisor_task: asyncio.Task[None] | None = None
        self._iex_task: asyncio.Task[None] | None = None
        self._crypto_task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._subs_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._shutting_down = False
        self._iex_restart_requested = False
        self._crypto_restart_requested = False

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
            for task in (
                self._supervisor_task,
                self._iex_supervisor_task,
                self._crypto_supervisor_task,
                self._iex_task,
                self._crypto_task,
            )
            if task is not None
        ] + list(self._callback_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor_task = None
        self._iex_supervisor_task = None
        self._crypto_supervisor_task = None
        self._iex_task = None
        self._crypto_task = None
        self._callback_tasks.clear()
        await self._close_iex_ws()
        await self._close_crypto_ws()
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

    async def on_subscribe_crypto(self, canonical_ids: list[str]) -> None:
        to_subscribe: list[str] = []
        async with self._subs_lock:
            for pair in _crypto_pairs(canonical_ids):
                if pair in self._crypto_active:
                    continue
                if len(self._crypto_active) >= _CRYPTO_CAP:
                    ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
                        endpoint="crypto", reason="cap_exceeded"
                    ).inc()
                    log.warning(
                        "alpaca.streamer.crypto_cap_exceeded",
                        cap=_CRYPTO_CAP,
                        pair=pair,
                    )
                    continue
                self._crypto_active.add(pair)
                to_subscribe.append(pair)
            self._record_active_locked()
        if to_subscribe:
            await self._send_ws_subscribe_crypto(sorted(to_subscribe))
            await self._restart_crypto_loop("subscribe_replay")

    async def on_unsubscribe_crypto(self, canonical_ids: list[str]) -> None:
        to_unsubscribe: list[str] = []
        async with self._subs_lock:
            for pair in _crypto_pairs(canonical_ids):
                if pair not in self._crypto_active:
                    continue
                self._crypto_active.remove(pair)
                to_unsubscribe.append(pair)
            self._record_active_locked()
        if to_unsubscribe:
            await self._send_ws_unsubscribe_crypto(sorted(to_unsubscribe))

    async def on_resync_crypto(self, expected_canonical_ids: list[str]) -> None:
        expected_pairs = _crypto_pairs(expected_canonical_ids)
        if len(expected_pairs) > _CRYPTO_CAP:
            for pair in expected_pairs[_CRYPTO_CAP:]:
                ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
                    endpoint="crypto", reason="cap_exceeded"
                ).inc()
                log.warning(
                    "alpaca.streamer.crypto_cap_exceeded",
                    cap=_CRYPTO_CAP,
                    pair=pair,
                )
            expected_pairs = expected_pairs[:_CRYPTO_CAP]
        expected = set(expected_pairs)
        async with self._subs_lock:
            current = set(self._crypto_active)
            stale = current - expected
            new = expected - current
            self._crypto_active = set(expected)
            self._record_active_locked()
        if new:
            await self._send_ws_subscribe_crypto(sorted(new))
        if stale:
            await self._send_ws_unsubscribe_crypto(sorted(stale))

    async def _supervisor_loop(self) -> None:
        self._iex_supervisor_task = asyncio.create_task(
            self._endpoint_supervisor_loop("iex"),
            name="alpaca-iex-supervisor",
        )
        self._crypto_supervisor_task = asyncio.create_task(
            self._endpoint_supervisor_loop("crypto"),
            name="alpaca-crypto-supervisor",
        )
        await asyncio.gather(
            self._iex_supervisor_task,
            self._crypto_supervisor_task,
            return_exceptions=True,
        )

    async def _endpoint_supervisor_loop(self, endpoint: str) -> None:
        task = self._create_endpoint_task(endpoint)
        while not self._shutting_down:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning(
                    "alpaca.streamer.endpoint_crash",
                    endpoint=endpoint,
                    error=str(exc),
                )
            if self._shutting_down:
                return
            self._restart_done_task(endpoint)
            task = self._endpoint_task(endpoint)

    def _create_endpoint_task(self, endpoint: str) -> asyncio.Task[None]:
        if endpoint == "iex":
            self._iex_task = asyncio.create_task(
                self._iex_loop(), name="alpaca-iex-loop"
            )
            return self._iex_task
        self._crypto_task = asyncio.create_task(
            self._crypto_loop(), name="alpaca-crypto-loop"
        )
        return self._crypto_task

    def _endpoint_task(self, endpoint: str) -> asyncio.Task[None]:
        task = self._iex_task if endpoint == "iex" else self._crypto_task
        if task is None:
            return self._create_endpoint_task(endpoint)
        return task

    def _restart_done_task(self, endpoint: str) -> None:
        if endpoint == "iex":
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
        if self._crypto_restart_requested:
            self._crypto_restart_requested = False
        else:
            ALPACA_WS_RECONNECT_TOTAL.labels(
                endpoint="crypto", reason="loop_crash"
            ).inc()
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

    async def _crypto_loop(self) -> None:
        backoff_attempt = 0
        while not self._shutting_down:
            try:
                await self._connect_crypto_once()
                backoff_attempt = 0
            except (ConnectionClosed, OSError) as exc:
                if self._shutting_down:
                    return
                ALPACA_WS_RECONNECT_TOTAL.labels(
                    endpoint="crypto", reason="ws_close"
                ).inc()
                log.warning("alpaca.streamer.crypto_disconnect", error=str(exc))
                await asyncio.sleep(min(2**backoff_attempt, _BACKOFF_MAX_SEC))
                backoff_attempt += 1

    async def _connect_crypto_once(self) -> None:
        api_key, api_secret = await self._auth.get_credentials()
        async with websockets.connect(
            config.BASE_URL_DATA_CRYPTO,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._crypto_ws = ws
            try:
                await self._authenticate(ws, api_key, api_secret, endpoint="crypto")
                await self._replay_crypto_subscriptions()
                while not self._shutting_down:
                    self._dispatch_frame(await ws.recv(), endpoint="crypto")
            finally:
                if self._crypto_ws is ws:
                    self._crypto_ws = None

    async def _authenticate(
        self,
        ws: _WebSocket,
        api_key: str,
        api_secret: str,
        *,
        endpoint: str = "iex",
    ) -> None:
        await ws.send(
            json.dumps({"action": "auth", "key": api_key, "secret": api_secret})
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=_AUTH_TIMEOUT_SEC)
        if not _is_auth_success(_load_frame(raw)):
            reason = "auth_rejected" if endpoint == "iex" else "auth_fail"
            ALPACA_WS_RECONNECT_TOTAL.labels(endpoint=endpoint, reason=reason).inc()
            raise RuntimeError(f"alpaca {endpoint} auth rejected")
        log.info("alpaca.streamer.auth_ok", endpoint=endpoint)

    async def _replay_iex_subscriptions(self) -> None:
        async with self._subs_lock:
            active = sorted(self._iex_active)
        if active:
            await self._send_ws_subscribe(active)

    async def _replay_crypto_subscriptions(self) -> None:
        async with self._subs_lock:
            active = sorted(self._crypto_active)
        if active:
            await self._send_ws_subscribe_crypto(active)

    async def _send_ws_subscribe(self, symbols: list[str]) -> None:
        if self._iex_ws is None or not symbols:
            return
        await self._iex_ws.send(
            json.dumps(
                {"action": "subscribe", "trades": [], "quotes": symbols, "bars": []}
            )
        )
        self._iex_pending_subs.extend(symbols)
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

    async def _send_ws_subscribe_crypto(self, pairs: list[str]) -> None:
        if self._crypto_ws is None or not pairs:
            return
        await self._crypto_ws.send(
            json.dumps(
                {"action": "subscribe", "trades": [], "quotes": pairs, "bars": []}
            )
        )
        self._crypto_pending_subs.extend(pairs)
        log.info("alpaca.streamer.crypto_subscribe", pairs=pairs)

    async def _send_ws_unsubscribe_crypto(self, pairs: list[str]) -> None:
        if self._crypto_ws is None or not pairs:
            return
        await self._crypto_ws.send(
            json.dumps(
                {"action": "unsubscribe", "trades": [], "quotes": pairs, "bars": []}
            )
        )
        log.info("alpaca.streamer.crypto_unsubscribe", pairs=pairs)

    async def _restart_iex_loop(self, reason: str) -> None:
        ALPACA_WS_RECONNECT_TOTAL.labels(endpoint="iex", reason=reason).inc()
        task = self._iex_task
        if task is None or task.done():
            return
        self._iex_restart_requested = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _restart_crypto_loop(self, reason: str) -> None:
        ALPACA_WS_RECONNECT_TOTAL.labels(endpoint="crypto", reason=reason).inc()
        task = self._crypto_task
        if task is None or task.done():
            return
        self._crypto_restart_requested = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _dispatch_frame(self, raw: str | bytes, *, endpoint: str = "iex") -> None:
        for item in _load_frame(raw):
            msg_type = item.get("T")
            if msg_type in {"q", "t"}:
                self._clear_pending_subs(endpoint)
                self._dispatch_quote(item, endpoint=endpoint)
                continue
            if msg_type == "error":
                self._handle_error(item, endpoint=endpoint)
                continue
            self._clear_pending_subs(endpoint)

    def _dispatch_quote(self, row: dict[str, Any], *, endpoint: str) -> None:
        quote = self._row_to_quote(row, endpoint=endpoint)
        if quote is None or self.tick_callback is None:
            return
        callback = self.tick_callback
        task = asyncio.create_task(
            self._invoke_tick_callback(callback, quote),
            name="alpaca-quote-callback",
        )
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)
        ALPACA_QUOTE_TICKS_TOTAL.labels(endpoint=endpoint, mode=config.MODE).inc()

    async def _invoke_tick_callback(
        self,
        callback: Callable[[pb.QuoteMessage], Awaitable[None]],
        quote: pb.QuoteMessage,
    ) -> None:
        try:
            await callback(quote)
        except Exception as exc:
            log.warning("alpaca.streamer.tick_callback_error", exc_info=exc)

    def _row_to_quote(
        self, row: dict[str, Any], *, endpoint: str = "iex"
    ) -> pb.QuoteMessage | None:
        raw_symbol = str(row.get("S") or "")
        if not raw_symbol:
            return None
        received_at = Timestamp()
        received_at.FromDatetime(datetime.now(UTC))
        tick_time = Timestamp()
        _set_tick_time(tick_time, row.get("t"))
        canonical_id = self._canonical_id_for_row(raw_symbol, endpoint=endpoint)
        return pb.QuoteMessage(
            canonical_id=canonical_id,
            tick_time=tick_time,
            received_at=received_at,
            source="alpaca",
            last=_decimal_str(row.get("p")),
            bid=_decimal_str(row.get("bp")),
            ask=_decimal_str(row.get("ap")),
            raw_payload=json.dumps(row, separators=(",", ":")).encode(),
        )

    def _canonical_id_for_row(self, raw_symbol: str, *, endpoint: str) -> str:
        if endpoint == "crypto":
            return normalize.alpaca_crypto_to_canonical(raw_symbol)
        return self._iex_symbol_map.get(raw_symbol, raw_symbol)

    def _handle_error(self, row: dict[str, Any], *, endpoint: str = "iex") -> None:
        code = _error_code(row.get("code"))
        reason = _SUBSCRIBE_REJECTION_CODES.get(code)
        if reason is not None:
            self._handle_subscribe_rejection(row, endpoint=endpoint, reason=reason)
            return
        if endpoint == "crypto":
            reason = "entitlement" if code in {402, 403} else "unknown"
            ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
                endpoint="crypto", reason=reason
            ).inc()
            log.warning("alpaca.streamer.crypto_error", code=code, msg=row.get("msg"))
            return
        if code == 410:
            log.warning("alpaca.streamer.iex_symbol_not_subscribed", msg=row.get("msg"))
            return
        log.warning("alpaca.streamer.iex_error", code=code, msg=row.get("msg"))

    def _handle_subscribe_rejection(
        self,
        row: dict[str, Any],
        *,
        endpoint: str,
        reason: str,
    ) -> None:
        ALPACA_UPSTREAM_SUBSCRIBE_REJECTED_TOTAL.labels(
            endpoint=endpoint, reason=reason
        ).inc()
        raw_symbol = self._rejected_symbol(row, endpoint=endpoint)
        canonical_id = self._canonical_id_for_row(raw_symbol, endpoint=endpoint)
        if endpoint == "crypto":
            self._crypto_active.discard(raw_symbol)
        else:
            self._iex_active.discard(raw_symbol)
            self._iex_symbol_map.pop(raw_symbol, None)
        self._record_active_locked()
        log.warning(
            "alpaca.streamer.subscribe_rejected",
            endpoint=endpoint,
            reason=reason,
            code=row.get("code"),
            symbol=raw_symbol,
            canonical_id=canonical_id,
            msg=row.get("msg"),
        )
        self._dispatch_drift_sentinel(canonical_id, reason)

    def _rejected_symbol(self, row: dict[str, Any], *, endpoint: str) -> str:
        active = self._crypto_active if endpoint == "crypto" else self._iex_active
        for candidate in (row.get("symbol"), row.get("S")):
            if isinstance(candidate, str) and candidate:
                return candidate
        msg = str(row.get("msg") or "")
        for candidate in sorted(active, key=len, reverse=True):
            if candidate and re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", msg):
                return candidate
        pending = (
            self._crypto_pending_subs
            if endpoint == "crypto"
            else self._iex_pending_subs
        )
        if pending:
            return pending.pop()
        return ""

    def _dispatch_drift_sentinel(self, canonical_id: str, reason: str) -> None:
        if not canonical_id or self.tick_callback is None:
            return
        quote = pb.QuoteMessage(
            canonical_id=canonical_id,
            source="alpaca",
            raw_payload=_DRIFT_SENTINELS.get(reason, _DRIFT_SENTINELS["unknown"]),
        )
        callback = self.tick_callback
        task = asyncio.create_task(
            self._invoke_tick_callback(callback, quote),
            name="alpaca-drift-callback",
        )
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    def _clear_pending_subs(self, endpoint: str) -> None:
        if endpoint == "crypto":
            self._crypto_pending_subs.clear()
            return
        self._iex_pending_subs.clear()

    async def _close_iex_ws(self) -> None:
        ws = self._iex_ws
        self._iex_ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def _close_crypto_ws(self) -> None:
        ws = self._crypto_ws
        self._crypto_ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _record_active_locked(self) -> None:
        ALPACA_SUBSCRIPTION_ACTIVE.labels(endpoint="iex", mode=config.MODE).set(
            len(self._iex_active)
        )
        ALPACA_SUBSCRIPTION_ACTIVE.labels(endpoint="crypto", mode=config.MODE).set(
            len(self._crypto_active)
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


def _crypto_pairs(canonical_ids: list[str]) -> list[str]:
    pairs: list[str] = []
    for canonical_id in canonical_ids:
        pairs.append(normalize.canonical_to_alpaca_crypto(canonical_id))
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


def _error_code(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        log.warning("alpaca.streamer.bad_error_code", value=value, exc_info=exc)
        return None


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
