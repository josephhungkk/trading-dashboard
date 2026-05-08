"""Schwab LEVELONE_EQUITIES websocket streamer for Phase 7b.1 quotes."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Self

import httpx
import structlog
import websockets
from google.protobuf.timestamp_pb2 import Timestamp
from websockets.exceptions import ConnectionClosed

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.auth import TokenCache
from sidecar_schwab.metrics import (
    SCHWAB_STREAMER_RECONNECT_TOTAL,
    SCHWAB_STREAMER_TICKS_TOTAL,
    SCHWAB_STREAMER_TOKEN_ROTATION_GAP_SECONDS,
)

log = structlog.get_logger(module="sidecar_schwab.streamer")

_USER_PREF_URL = "https://api.schwabapi.com/trader/v1/userPreference"
_FIELD_MASK = "0,1,2,3,8,12,28,29,30,33"
_BACKOFF_MAX_SEC = 60.0
_LOGIN_TIMEOUT_SEC = 10.0
_IDLE_TIMEOUT_SEC = 90.0
# Hard cap on the per-streamer refcount table. Schwab's per-account L1
# entitlement is well below this; the cap is a runaway-caller / DoS guard.
_MAX_SYMBOLS = 5000


class _WebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


@dataclass(slots=True)
class _SymbolEntry:
    raw_symbol: str
    refcount: int


class SchwabStreamer:
    """Single Schwab websocket session with gRPC-driven subscription callbacks."""

    def __init__(
        self,
        token_cache: TokenCache,
        tokens_refreshed: asyncio.Event,
        *,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._token_cache = token_cache
        self._tokens_refreshed = tokens_refreshed
        self._http_factory = http_factory or httpx.AsyncClient
        self.tick_callback: Callable[[pb.QuoteMessage], None] | None = None
        self._ws: _WebSocket | None = None
        self._streamer_info: dict[str, Any] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._req_seq = itertools.count(start=1)
        self._started = False
        self._shutting_down = False
        self._start_lock = asyncio.Lock()
        self._subs_lock = asyncio.Lock()
        self._upstream_refcount: dict[str, _SymbolEntry] = {}
        # HIGH fix: O(1) reverse lookup raw_symbol → canonical_id.
        self._raw_to_canonical: dict[str, str] = {}

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self._bootstrap_and_connect()
            self._started = True
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="schwab-streamer-reader"
            )

    async def stop(self) -> None:
        self._shutting_down = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        await self._close_ws()
        self._started = False
        self._shutting_down = False

    async def on_subscribe(self, symbols: list[pb.SymbolRef]) -> None:
        commands: list[tuple[str, str]] = []
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id, raw_symbol = _ids(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is None:
                    if len(self._upstream_refcount) >= _MAX_SYMBOLS:
                        log.warning(
                            "schwab.streamer.subs_cap_hit",
                            current=len(self._upstream_refcount),
                            cap=_MAX_SYMBOLS,
                            canonical_id=canonical_id,
                        )
                        continue
                    self._upstream_refcount[canonical_id] = _SymbolEntry(raw_symbol, 1)
                    # HIGH fix: new symbol → send only SUBS (not SUBS+ADD).
                    # Sending both duplicates the subscription on the Schwab
                    # streamer side, causing double-counted ticks.
                    commands.append(("SUBS", raw_symbol))
                    # HIGH fix: maintain inverse map for O(1) tick dispatch.
                    self._raw_to_canonical[raw_symbol] = canonical_id
                    continue
                entry.refcount += 1
                # refcount > 1: upstream sub already live; no wire command needed.
        for command, raw_symbol in commands:
            await self._send_levelone(command, [raw_symbol])

    async def on_unsubscribe(self, symbols: list[pb.SymbolRef]) -> None:
        raw_to_unsub: list[str] = []
        async with self._subs_lock:
            for symbol in symbols:
                canonical_id, _ = _ids(symbol)
                entry = self._upstream_refcount.get(canonical_id)
                if entry is None:
                    continue
                entry.refcount -= 1
                if entry.refcount <= 0:
                    raw_to_unsub.append(entry.raw_symbol)
                    # HIGH fix: keep inverse map in sync.
                    self._raw_to_canonical.pop(entry.raw_symbol, None)
                    del self._upstream_refcount[canonical_id]
        for raw_symbol in raw_to_unsub:
            await self._send_levelone("UNSUBS", [raw_symbol])

    async def on_resync(self, expected: list[pb.SymbolRef]) -> None:
        async with self._subs_lock:
            expected_map = {_ids(symbol)[0]: _ids(symbol)[1] for symbol in expected}
            current_ids = set(self._upstream_refcount)
            expected_ids = set(expected_map)
            stale_entries = [
                (key, self._upstream_refcount[key].raw_symbol)
                for key in current_ids - expected_ids
            ]
            stale_raw = [raw for _, raw in stale_entries]
            new = [expected_map[key] for key in expected_ids - current_ids]
            for canonical_id, raw_symbol in stale_entries:
                # HIGH fix: keep inverse map in sync on resync drop.
                self._raw_to_canonical.pop(raw_symbol, None)
                del self._upstream_refcount[canonical_id]
            for canonical_id in expected_ids - current_ids:
                raw_symbol = expected_map[canonical_id]
                self._upstream_refcount[canonical_id] = _SymbolEntry(raw_symbol, 1)
                # HIGH fix: seed inverse map for newly-added entries.
                self._raw_to_canonical[raw_symbol] = canonical_id
        if new:
            await self._send_levelone("SUBS", sorted(new))
        if stale_raw:
            await self._send_levelone("UNSUBS", sorted(stale_raw))

    async def _reader_loop(self) -> None:
        backoff_attempt = 0
        while not self._shutting_down:
            reason = await self._recv_until_reconnect()
            if self._shutting_down:
                return
            # Token-rotation gap = full rotation cycle (event detected →
            # reconnect + replay complete). Measured here, not inside
            # _record_reconnect, so the metric reflects user-visible
            # downtime, not just close_ws() duration.
            rotation_started = (
                time.monotonic() if reason == "token_rotation" else None
            )
            await self._record_reconnect(reason)
            if reason != "token_rotation":
                # HIGH fix: cap exponent at 6 (2^6=64 > _BACKOFF_MAX_SEC=60)
                # so 2**backoff_attempt never overflows for large attempt counts.
                await asyncio.sleep(min(2 ** min(backoff_attempt, 6), _BACKOFF_MAX_SEC))
                backoff_attempt += 1
            if await self._reconnect_with_new_creds():
                backoff_attempt = 0
            if rotation_started is not None:
                SCHWAB_STREAMER_TOKEN_ROTATION_GAP_SECONDS.observe(
                    time.monotonic() - rotation_started
                )

    async def _recv_until_reconnect(self) -> str:
        while self._ws is not None and not self._shutting_down:
            recv_task = asyncio.create_task(self._ws.recv())
            rotation_task = asyncio.create_task(self._tokens_refreshed.wait())
            done, pending = await asyncio.wait(
                {recv_task, rotation_task},
                timeout=_IDLE_TIMEOUT_SEC,
                return_when=asyncio.FIRST_COMPLETED,
            )
            await _cancel_pending(pending)
            if not done:
                return "idle"
            if rotation_task in done and self._tokens_refreshed.is_set():
                return "token_rotation"
            try:
                self._dispatch_frame(recv_task.result())
            except ConnectionClosed:
                return "ws_close"
            except Exception as exc:  # noqa: BLE001
                log.warning("schwab.streamer.frame_error", error=str(exc))
        return "ws_close"

    async def _record_reconnect(self, reason: str) -> None:
        log.info("schwab.streamer.reconnect", reason=reason)
        SCHWAB_STREAMER_RECONNECT_TOTAL.labels(reason=reason).inc()
        if reason == "token_rotation":
            log.info("schwab.streamer.token_rotation_reconnect")
            self._tokens_refreshed.clear()
        await self._close_ws()

    async def _reconnect_with_new_creds(self) -> bool:
        try:
            await self._bootstrap_and_connect()
            await self._replay_subscriptions()
        except Exception as exc:  # noqa: BLE001
            log.warning("schwab.streamer.reconnect_failed", error=str(exc))
            return False
        return True

    async def _bootstrap_and_connect(self) -> None:
        access = await self._token_cache.get_access_token()
        info = await self._fetch_streamer_info(access)
        self._streamer_info = info
        socket_url = str(info.get("streamerSocketUrl") or "")
        if not socket_url:
            raise RuntimeError("streamerInfo missing streamerSocketUrl")
        # Defense-in-depth: a compromised Schwab response that returns a
        # plaintext ws:// or attacker-controlled host would receive the
        # bearer token in the LOGIN frame in the clear. Reject any URL
        # that isn't WSS to a Schwab-controlled domain.
        if not socket_url.startswith("wss://"):
            raise RuntimeError(
                f"streamerSocketUrl is not WSS: {socket_url!r}"
            )
        from urllib.parse import urlparse

        host = (urlparse(socket_url).hostname or "").lower()
        if not (host.endswith(".schwab.com") or host.endswith(".schwabapi.com")):
            raise RuntimeError(f"streamerSocketUrl host not Schwab: {host!r}")
        self._ws = await websockets.connect(
            socket_url, ping_interval=30, ping_timeout=10, close_timeout=5
        )
        await self._login(info, access)

    async def _fetch_streamer_info(self, access: str) -> dict[str, Any]:
        async with self._http_factory() as http:
            resp = await http.get(
                _USER_PREF_URL,
                headers={"Authorization": f"Bearer {access}"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"userPreference status {resp.status_code}")
        payload = resp.json()
        info = (payload.get("streamerInfo") or [None])[0]
        if not isinstance(info, dict):
            raise RuntimeError("userPreference missing streamerInfo")
        return info

    async def _login(self, info: dict[str, Any], access: str) -> None:
        if self._ws is None:
            raise RuntimeError("websocket not connected")
        req = self._base_request(info, "ADMIN", "LOGIN")
        req["parameters"] = {
            "Authorization": access,
            "SchwabClientChannel": info.get("schwabClientChannel"),
            "SchwabClientFunctionId": info.get("schwabClientFunctionId"),
        }
        await self._ws.send(json.dumps({"requests": [req]}))
        raw = await asyncio.wait_for(self._ws.recv(), timeout=_LOGIN_TIMEOUT_SEC)
        if not _is_login_success(_load_frame(raw), str(req["requestid"])):
            SCHWAB_STREAMER_RECONNECT_TOTAL.labels(reason="login_fail").inc()
            raise RuntimeError("schwab LOGIN rejected")
        log.info("schwab.streamer.login_ok")

    async def _replay_subscriptions(self) -> None:
        async with self._subs_lock:
            raw_symbols = [
                entry.raw_symbol for entry in self._upstream_refcount.values()
            ]
        if raw_symbols:
            await self._send_levelone("SUBS", sorted(raw_symbols))

    async def _send_levelone(self, command: str, raw_symbols: list[str]) -> None:
        if self._ws is None or self._streamer_info is None or not raw_symbols:
            return
        req = self._base_request(self._streamer_info, "LEVELONE_EQUITIES", command)
        req["parameters"] = {"keys": ",".join(raw_symbols), "fields": _FIELD_MASK}
        await self._ws.send(json.dumps({"requests": [req]}))
        # MED fix: subscription wire commands are noisy at INFO; demote to DEBUG.
        log.debug("schwab.streamer.subs", command=command, symbols=raw_symbols)

    def _base_request(
        self, info: dict[str, Any], service: str, command: str
    ) -> dict[str, Any]:
        return {
            "requestid": str(next(self._req_seq)),
            "service": service,
            "command": command,
            "SchwabClientCustomerId": info.get("schwabClientCustomerId"),
            "SchwabClientCorrelId": info.get("schwabClientCorrelId"),
        }

    def _dispatch_frame(self, raw: str | bytes) -> None:
        frame = _load_frame(raw)
        if "notify" in frame:
            return
        if "response" in frame:
            _log_command_errors(frame)
            return
        for block in frame.get("data") or []:
            if block.get("service") != "LEVELONE_EQUITIES":
                continue
            self._dispatch_rows(block.get("content") or [])

    def _dispatch_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            quote = self._row_to_quote(row)
            if quote is None or self.tick_callback is None:
                continue
            try:
                self.tick_callback(quote)
            except Exception as exc:  # noqa: BLE001
                # Isolate per-tick callback failures from the recv loop —
                # one bad consumer must not stall the WS or kill the task.
                log.warning(
                    "schwab.streamer.tick_callback_error", error=str(exc)
                )
                continue
            raw_symbol = _row_symbol(row)
            SCHWAB_STREAMER_TICKS_TOTAL.labels(symbol=raw_symbol).inc()
            log.info("schwab.streamer.tick", symbol=raw_symbol)

    def _row_to_quote(self, row: dict[str, Any]) -> pb.QuoteMessage | None:
        raw_symbol = _row_symbol(row)
        canonical_id = self._canonical_for_raw(raw_symbol)
        if not canonical_id:
            return None
        received_at = Timestamp()
        received_at.FromDatetime(datetime.now(timezone.utc))
        return pb.QuoteMessage(
            canonical_id=canonical_id,
            received_at=received_at,
            source="schwab",
            last=_decimal_str(row.get("3")),
            bid=_decimal_str(row.get("1")),
            ask=_decimal_str(row.get("2")),
            volume=_int_str(row.get("8")),
            prev_close=_decimal_str(row.get("12")),
            day_high=_decimal_str(row.get("28")),
            day_low=_decimal_str(row.get("29")),
            open=_decimal_str(row.get("30")),
            change_pct=_decimal_str(row.get("33")),
            raw_payload=json.dumps(row, separators=(",", ":")).encode(),
        )

    def _canonical_for_raw(self, raw_symbol: str) -> str:
        # HIGH fix: O(1) lookup via inverse map instead of O(n) linear scan.
        return self._raw_to_canonical.get(raw_symbol, "")

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    @classmethod
    def for_tests(cls, tokens_refreshed: asyncio.Event | None = None) -> Self:
        token_cache = TokenCache(refresh_client=None)
        token_cache.set_tokens("A", "R", datetime.now(timezone.utc))
        return cls(token_cache, tokens_refreshed or asyncio.Event())


def _ids(symbol: pb.SymbolRef) -> tuple[str, str]:
    raw_symbol = symbol.raw_symbol or symbol.canonical_id
    canonical_id = symbol.canonical_id or raw_symbol
    return canonical_id, raw_symbol


def _load_frame(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    frame = json.loads(raw)
    if not isinstance(frame, dict):
        return {}
    return frame


def _is_login_success(frame: dict[str, Any], requestid: str) -> bool:
    for response in frame.get("response") or []:
        if response.get("command") != "LOGIN":
            continue
        if str(response.get("requestid")) != requestid:
            continue
        content = response.get("content") or {}
        return content.get("code") == 0
    return False


def _log_command_errors(frame: dict[str, Any]) -> None:
    for response in frame.get("response") or []:
        content = response.get("content") or {}
        if content.get("code") in (0, None):
            continue
        log.warning(
            "schwab.streamer.command_error",
            service=response.get("service"),
            command=response.get("command"),
            code=content.get("code"),
            msg=content.get("msg"),
        )


def _row_symbol(row: dict[str, Any]) -> str:
    value = row.get("key") or row.get("0") or row.get(0) or ""
    return str(value)


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


async def _cancel_pending(pending: set[asyncio.Task[Any]]) -> None:
    """Cancel + await pending tasks so their coroutine frames are released
    immediately instead of being kept alive until GC. Phase 7b.1 B4 lesson
    applied here too — bare ``cancel()`` is request-only.
    """
    for task in pending:
        if not task.done():
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
