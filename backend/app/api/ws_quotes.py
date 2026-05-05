"""MessagePack quote WebSocket gateway."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any, Literal, cast
from uuid import uuid4

import msgpack  # type: ignore[import-untyped]
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, ValidationError

from app._generated.broker.v1 import broker_pb2 as pb
from app.api.ws_auth import require_admin_jwt_ws
from app.core.metrics import (
    QUOTE_WS_CONNECTIONS,
    QUOTE_WS_RECV_INVALID_TOTAL,
    QUOTE_WS_SEND_TIMEOUT_TOTAL,
    QUOTE_WS_SEND_TOTAL,
)
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import WSConnId

router = APIRouter(tags=["quotes-ws"])

_SUBPROTOCOL = "msgpack-v1"
_SEND_TIMEOUT_SECONDS = 2.0


class ClientFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    op: Literal["sub", "unsub", "focus"]
    symbols: list[str] | None = None
    canonical_id: str | None = None


_log = structlog.get_logger(__name__)


class WSConflator:
    """Per-connection latest-only quote conflator."""

    def __init__(
        self,
        ws: WebSocket,
        focused_default: str | None = None,
        ws_id: str | None = None,
    ) -> None:
        self._ws = ws
        self._pending: dict[str, pb.QuoteMessage] = {}
        self._last_sent: dict[str, float] = {}
        self._focused_symbol = focused_default
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._ws_id = ws_id  # forensic log marker only — engine uses its own UUID

    def on_quote(self, q: pb.QuoteMessage) -> None:
        self._pending[q.canonical_id] = q

    def set_focus(self, canonical_id: str | None) -> None:
        self._focused_symbol = canonical_id

    async def run(self, rate_focused: int = 10, rate_background: int = 4) -> None:
        period = 1.0 / max(rate_focused, rate_background)
        while not self._closed:
            await asyncio.sleep(period)
            await self._drain_once(rate_focused=rate_focused, rate_background=rate_background)

    async def send_frame(self, frame: dict[str, Any]) -> bool:
        op = str(frame.get("op", "unknown"))
        payload = msgpack.packb(frame, use_bin_type=True)
        async with self._send_lock:
            try:
                await asyncio.wait_for(self._ws.send_bytes(payload), timeout=2.0)
            except TimeoutError:
                QUOTE_WS_SEND_TIMEOUT_TOTAL.inc()
                await self._close_slow_client()
                return False
        QUOTE_WS_SEND_TOTAL.labels(op=op).inc()
        return True

    async def _drain_once(self, *, rate_focused: int, rate_background: int) -> None:
        now = time.monotonic()
        for sym in list(self._pending):
            rate = rate_focused if sym == self._focused_symbol else rate_background
            if now - self._last_sent.get(sym, 0.0) < 1.0 / rate:
                continue
            quote = self._pending.pop(sym, None)
            if quote is None:
                continue
            self._last_sent[sym] = now
            if not await self.send_frame(_quote_frame("q", quote)):
                return

    async def _close_slow_client(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Sec M2: emit a structured log with ws identity + peer IP on every
        # slow-client close so dashboards can distinguish a single laggy
        # consumer from a slow-loris-style targeted DoS.
        client = getattr(self._ws, "client", None)
        peer_ip = client.host if client is not None else None
        _log.warning(
            "ws_quotes.slow_client_closed",
            ws_id=self._ws_id,
            peer_ip=peer_ip,
        )
        await self._ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="slow-client")


def get_quote_engine(ws: WebSocket) -> QuoteEngine:
    engine = getattr(ws.app.state, "quote_engine", None)
    if engine is None:
        raise WebSocketException(code=status.WS_1011_INTERNAL_ERROR, reason="quote-engine")
    return cast(QuoteEngine, engine)


@router.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket) -> None:
    try:
        await require_admin_jwt_ws(ws)
    except WebSocketException:
        return
    if not _supports_msgpack(ws):
        await ws.close(code=status.WS_1002_PROTOCOL_ERROR, reason="subprotocol")
        return

    engine = get_quote_engine(ws)
    await ws.accept(subprotocol=_SUBPROTOCOL)
    QUOTE_WS_CONNECTIONS.inc()

    ws_id = uuid4()
    conflator = WSConflator(ws, focused_default=None, ws_id=str(ws_id))
    engine.register_conflator(ws_id, conflator.on_quote)
    task = asyncio.create_task(conflator.run())
    try:
        while True:
            await _handle_raw_frame(ws, ws_id, engine, conflator)
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await engine.disconnect_ws(ws_id)
        await _cancel_and_await(task)
        QUOTE_WS_CONNECTIONS.dec()


async def _handle_raw_frame(
    ws: WebSocket,
    ws_id: WSConnId,
    engine: QuoteEngine,
    conflator: WSConflator,
) -> None:
    try:
        raw = await ws.receive_bytes()
        # Cap deserialisation to defang attacker-controlled msgpack frames —
        # ClientFrame has 3 fields, so generous-but-bounded caps eliminate
        # the unbounded-allocation risk with no functional impact (Sec M1).
        unpacked = msgpack.unpackb(
            raw,
            raw=False,
            strict_map_key=False,
            max_str_len=512,
            max_bin_len=512,
            max_array_len=1000,
            max_map_len=32,
        )
        frame = ClientFrame.model_validate(unpacked)
    except ValidationError as exc:
        reason = _validation_reason(exc)
        QUOTE_WS_RECV_INVALID_TOTAL.labels(reason=reason).inc()
        await conflator.send_frame(_err_frame(reason))
        return
    except (
        TypeError,
        ValueError,
        msgpack.ExtraData,
        msgpack.FormatError,
        msgpack.StackError,
    ):
        QUOTE_WS_RECV_INVALID_TOTAL.labels(reason="bad_msgpack").inc()
        await conflator.send_frame(_err_frame("bad_msgpack"))
        return

    await _dispatch_frame(ws_id, frame, engine, conflator)


async def _dispatch_frame(
    ws_id: WSConnId,
    frame: ClientFrame,
    engine: QuoteEngine,
    conflator: WSConflator,
) -> None:
    if frame.op == "focus":
        conflator.set_focus(frame.canonical_id)
        await conflator.send_frame(_ack_frame("focus", [], []))
        return

    symbols = frame.symbols or []
    if not symbols:
        QUOTE_WS_RECV_INVALID_TOTAL.labels(reason="missing_field").inc()
        await conflator.send_frame(_err_frame("missing_field"))
        return

    if frame.op == "sub":
        diff = await engine.subscribe(ws_id, symbols)
        await _send_snapshots(diff.added, engine, conflator)
        await conflator.send_frame(_ack_frame("sub", diff.added, diff.rejected))
        return

    unsub_diff = await engine.unsubscribe(ws_id, symbols)
    await conflator.send_frame(_ack_frame("unsub", unsub_diff.removed, []))


async def _send_snapshots(
    symbols: Iterable[str],
    engine: QuoteEngine,
    conflator: WSConflator,
) -> None:
    for sym in symbols:
        cached = engine.get_cached(sym)
        if cached is not None and not await conflator.send_frame(_quote_frame("snap", cached)):
            return


async def _cancel_and_await(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


def _supports_msgpack(ws: WebSocket) -> bool:
    header = ws.headers.get("sec-websocket-protocol", "")
    return _SUBPROTOCOL in {part.strip() for part in header.split(",")}


def _validation_reason(exc: ValidationError) -> Literal["bad_op", "missing_field"]:
    for err in exc.errors():
        if err.get("loc") == ("op",):
            return "bad_op"
    return "missing_field"


def _quote_frame(op: Literal["q", "snap", "stale"], q: pb.QuoteMessage) -> dict[str, Any]:
    return {
        "op": op,
        "sym": q.canonical_id,
        "data": MessageToDict(q, preserving_proto_field_name=True),
    }


def _ack_frame(op: str, accepted: Iterable[str], rejected: Iterable[str]) -> dict[str, Any]:
    return {
        "op": "ack",
        "sym": "",
        "data": {
            "op": op,
            "accepted": sorted(str(sym) for sym in accepted),
            "rejected": sorted(str(sym) for sym in rejected),
        },
    }


def _err_frame(reason: str) -> dict[str, Any]:
    return {"op": "err", "sym": "", "data": {"reason": reason}}
