"""Historical bars HTTP router + WS live-tail gateway (spec §4 lines 599-611).

Endpoints:
  GET /api/bars?canonical_id=&timeframe=&start=&end=&limit=10000&cursor=
  WS  /ws/bars/<canonical_id>/<timeframe>

GET is a thin wrapper over BarService.get_bars.  All pagination logic and
cursor encoding live in the service layer (bar_service.py).

WS gateway subscribes to Redis pub/sub channel ``bar.<canonical_id>.<tf>``
(published by bar_pubsub.py from Chunk B).  Auth uses the token-via-subprotocol
pattern (``Sec-WebSocket-Protocol: bearer.<jwt>``).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from jwt.exceptions import PyJWTError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import deps as _deps_mod
from app.core.cf_access import NoIdentityClaimError, client_ip_in_trusted_nets
from app.core.config import settings
from app.core.deps import get_db, require_admin_jwt
from app.services.bar_service import (
    BarFetchTooLarge,
    BarPage,
    BarService,
    BarSourceUnavailable,
    InstrumentNotFound,
    InvalidCursor,
)

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["bars"],
    dependencies=[Depends(require_admin_jwt)],
)

# WS router — no HTTP-level auth dependency (auth handled inside the WS handler)
ws_router = APIRouter(tags=["bars-ws"])

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ---------------------------------------------------------------------------
# Pydantic models — HTTP + WS
# ---------------------------------------------------------------------------

# Sentinel revision value for final (closed-bucket) bars.  Must match
# FINAL_REVISION in bar_pubsub.py (Chunk B).
_FINAL_REVISION: int = 2**31 - 1

_WS_PING_INTERVAL: float = 60.0  # seconds between server-initiated pings
_WS_PONG_TIMEOUT: float = 30.0  # seconds client has to reply with pong
_WS_DEFAULT_MAX_SUBS: int = 20  # fallback if app_config key is missing

# MED-27: conservative pattern for dashboard canonical_id format (e.g. AAPL.US, BTC-USD.CRYPTO)
_CANONICAL_ID_RE = re.compile(r"^[A-Z0-9._-]{1,64}$")

# Allowed WS timeframes
_VALID_TIMEFRAMES = frozenset({"1s", "1m", "5m", "15m", "30m", "1h", "1d"})

# MED-21: module-level cache for ws_max_subs; (value, expires_at) keyed by "v"
_WS_MAX_SUBS_CACHE: dict[str, tuple[int, float]] = {}


class BarItem(BaseModel):
    """Single bar row in the paginated response."""

    bucket_start: datetime  # ISO8601 UTC
    open: str  # NUMERIC(20,8) preserved as string — no float coercion
    high: str
    low: str
    close: str
    volume: str
    trade_count: int


class BarsPageResponse(BaseModel):
    """Paginated bars response (spec §4)."""

    bars: list[BarItem]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


def _get_bar_service(request: Request) -> BarService:
    """Return the BarService singleton wired in main.py lifespan.

    Raises 503 if accessed before lifespan has completed startup.
    """
    svc: BarService | None = getattr(request.app.state, "bar_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="bar_service_not_ready")
    return svc


# ---------------------------------------------------------------------------
# GET /api/bars
# ---------------------------------------------------------------------------


@router.get("/bars", response_model=BarsPageResponse)
async def get_bars(
    request: Request,
    db: DbDep,
    canonical_id: Annotated[str, Query(...)],
    timeframe: Annotated[
        str,
        Query(pattern=r"^(1s|1m|5m|15m|30m|1h|1d)$"),
    ],
    start: Annotated[datetime, Query(...)],
    end: Annotated[datetime, Query(...)],
    limit: Annotated[int, Query(gt=0, le=10000)] = 10000,
    cursor: Annotated[str | None, Query()] = None,
) -> BarsPageResponse:
    """Return a paginated page of historical bars for one instrument + timeframe.

    Parameters
    ----------
    canonical_id:
        Instrument canonical identifier, e.g. ``"equity_us:AAPL:NASDAQ"``.
    timeframe:
        Bar timeframe — one of ``1s``, ``1m``, ``5m``, ``15m``, ``30m``,
        ``1h``, ``1d``.
    start:
        Inclusive range start (UTC ISO8601).
    end:
        Exclusive range end (UTC ISO8601).
    limit:
        Maximum bars per page (1-10000, default 10000).
    cursor:
        Opaque pagination cursor from a previous response.  Omit for first
        page.

    Raises
    ------
    400 ``invalid_cursor``       — cursor is malformed or has unknown version.
    404 ``instrument_not_found`` — canonical_id not in instruments table.
    413 ``bar_fetch_too_large``  — backfill exceeded the 100-chunk hard cap.
    503 ``bar_source_unavailable`` — no healthy sidecar for this asset class.
    """
    bar_service = _get_bar_service(request)

    try:
        page: BarPage = await bar_service.get_bars(
            canonical_id=canonical_id,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            cursor=cursor,
            session=db,
        )
    except InstrumentNotFound as exc:
        raise HTTPException(status_code=404, detail="instrument_not_found") from exc
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except BarFetchTooLarge as exc:
        raise HTTPException(status_code=413, detail="bar_fetch_too_large") from exc
    except BarSourceUnavailable as exc:
        raise HTTPException(status_code=503, detail="bar_source_unavailable") from exc

    return BarsPageResponse(
        bars=[
            BarItem(
                bucket_start=bar.bucket_start,
                open=str(bar.open),
                high=str(bar.high),
                low=str(bar.low),
                close=str(bar.close),
                volume=str(bar.volume) if bar.volume is not None else "0",
                trade_count=bar.trade_count,
            )
            for bar in page.bars
        ],
        next_cursor=page.next_cursor,
    )


# ---------------------------------------------------------------------------
# Task 31 — WS live-tail gateway
# ---------------------------------------------------------------------------


class BarEnvelope(BaseModel):
    """Wire envelope emitted to the client for each bar update (spec lines 778-787)."""

    canonical_id: str
    timeframe: str
    bucket_start: datetime  # ISO8601 UTC
    open: str
    high: str
    low: str
    close: str
    volume: str
    trade_count: int
    revision: int  # monotonic; partial=False ⇒ 2**31-1 (FINAL_REVISION)
    partial: bool


def _extract_bearer_jwt(ws: WebSocket) -> str | None:
    """Return the raw JWT from the first ``bearer.<token>`` subprotocol offered.

    Returns *None* if no matching subprotocol is present.  Does NOT verify the
    token — that is the caller's responsibility.
    """
    header = ws.headers.get("sec-websocket-protocol", "")
    for part in header.split(","):
        part = part.strip()
        if part.startswith("bearer."):
            return part[len("bearer.") :]
    return None


async def _ws_bars_auth(ws: WebSocket) -> bool:
    """Verify the bearer JWT extracted from the subprotocol header.

    Returns True on success (WS still not accepted yet).  Sends close-frame
    4001 and returns False on auth failure.  Never logs the token (codex G).
    """
    token = _extract_bearer_jwt(ws)
    if not token:
        await ws.close(code=4001, reason="unauthenticated")
        return False

    # WG dev bypass: if the client IP is the trusted dev host, allow through
    # without JWKS validation (mirrors ws_auth.py behaviour).
    client = getattr(ws, "client", None)
    client_ip: str = client.host if client is not None else ""
    bypass = _deps_mod._verifier.check_dev_bypass(client_ip)
    if bypass is not None:
        # HIGH-12: mirror the prod safety check from deps.py:require_admin_jwt.
        # If env=prod AND trusted_dev_nets is set, dev-bypass must never succeed —
        # treat as misconfiguration and close WS (prevents silent prod dev-bypass).
        if (
            settings.env == "prod"
            and settings.trusted_dev_nets
            and client_ip_in_trusted_nets(client_ip, settings.trusted_dev_nets)
        ):
            log.critical(
                "ws_bars.dev_bypass_attempted_in_prod",
                client_ip=client_ip,
            )
            await ws.close(code=4001, reason="unauthenticated")
            return False
        return True

    try:
        _deps_mod._verifier.verify(token, client_ip=client_ip)
    except NoIdentityClaimError, PyJWTError:
        await ws.close(code=4001, reason="unauthenticated")
        return False
    return True


async def _get_ws_max_subs() -> int:
    """Read ``charts.ws_max_subs_per_conn`` from app_config; fall back to 20.

    MED-21: cached with 60s TTL to avoid per-connection DB queries.
    """
    from app.core.db import SessionLocal  # local import — avoids circular dep at module level

    # Check cache first
    cached = _WS_MAX_SUBS_CACHE.get("v")
    now = time.monotonic()
    if cached is not None and cached[1] > now:
        return cached[0]

    try:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT value::int FROM app_config"
                        " WHERE namespace='charts'"
                        " AND key='ws_max_subs_per_conn'"
                    )
                )
            ).one_or_none()
            value = int(row[0]) if row else _WS_DEFAULT_MAX_SUBS
            _WS_MAX_SUBS_CACHE["v"] = (value, now + 60.0)
            return value
    except Exception as exc:
        log.warning("ws_bars.get_max_subs_failed", error=str(exc))
        return _WS_DEFAULT_MAX_SUBS


def _make_ping_frame() -> str:
    return json.dumps({"op": "ping", "ts": int(time.time())})


def _make_error_frame(detail: str) -> str:
    return json.dumps({"op": "error", "detail": detail})


@ws_router.websocket("/ws/bars/{canonical_id}/{timeframe}")
async def ws_bars(ws: WebSocket, canonical_id: str, timeframe: str) -> None:
    """WebSocket live-tail endpoint for bar updates (spec §4 lines 604, 607-611).

    Auth: ``Sec-WebSocket-Protocol: bearer.<jwt>``  (token-via-subprotocol).
    Subscribes to Redis pub/sub channel ``bar.<canonical_id>.<tf>``.
    """
    authed = await _ws_bars_auth(ws)
    if not authed:
        return

    # NOTE: the bearer.<jwt> echo back via Sec-WebSocket-Protocol is visible in
    # browser devtools / extensions / proxies. This is the established Phase 7b.1
    # ws_auth pattern. Phase 9.5 may negotiate a neutral subprotocol after
    # verification (requires coordinated FE update to offer ['bars-v1',
    # 'bearer.<jwt>'] subprotocols). Tracking as security-MED.
    await ws.accept(subprotocol="bearer." + (_extract_bearer_jwt(ws) or ""))

    # ── Config ──────────────────────────────────────────────────────────────
    max_subs: int = _WS_DEFAULT_MAX_SUBS
    try:
        max_subs = await _get_ws_max_subs()
    except Exception as exc:
        log.warning("ws_bars.startup_config_load_failed", error=str(exc))

    # ── State ────────────────────────────────────────────────────────────────
    # subscriptions: set of (canonical_id, timeframe) tuples active for this conn
    subscriptions: set[tuple[str, str]] = set()
    # Queue that the pubsub listener pushes decoded bar payloads onto
    bar_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    # Flag used to request graceful shutdown from idle watchdog or pubsub listener
    _closed = False
    redis_obj = getattr(ws.app.state, "redis", None)

    # ── Tasks tracked for cancel+gather on close (codex B) ──────────────────
    background_tasks: set[asyncio.Task[Any]] = set()

    async def _pubsub_listener() -> None:
        """Subscribe to all bar.* channels for active subs; fan messages to bar_queue."""
        if redis_obj is None:
            return
        pubsub = redis_obj.pubsub()
        subscribed_channels: set[str] = set()
        try:
            while not _closed:
                # Build desired channel set from current subscriptions
                desired = {f"bar.{cid}.{tf}" for cid, tf in subscriptions}
                to_add = desired - subscribed_channels
                to_remove = subscribed_channels - desired

                if to_add:
                    await pubsub.subscribe(*to_add)
                    subscribed_channels.update(to_add)
                if to_remove:
                    await pubsub.unsubscribe(*to_remove)
                    subscribed_channels -= to_remove

                # Poll for a message
                try:
                    msg = await asyncio.wait_for(pubsub.get_message(timeout=0.1), timeout=0.5)
                except TimeoutError:
                    msg = None

                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue

                # Per-message try/except — codex C: one bad payload must not kill conn
                try:
                    data_bytes = msg.get("data", b"")
                    if isinstance(data_bytes, (bytes, bytearray)):
                        payload = json.loads(data_bytes.decode())
                    else:
                        payload = json.loads(data_bytes)
                    bar_queue.put_nowait(payload)
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError, ValueError) as exc:
                    log.warning(
                        "ws_bars.pubsub.bad_payload",
                        channel=msg.get("channel"),
                        error=str(exc),
                    )
        finally:
            await pubsub.aclose()

    async def _ping_loop() -> None:
        """Send PING every 60s; close with 1000 idle_timeout if no PONG within 30s."""
        nonlocal _closed
        while not _closed:
            await asyncio.sleep(_WS_PING_INTERVAL)
            if _closed:
                break
            try:
                await ws.send_text(_make_ping_frame())
            except (RuntimeError, OSError) as exc:
                log.warning("ws_bars.ping_send_failed", error=str(exc))
                break
            # Wait for pong via bar_queue sentinel or timeout
            pong_received = False
            deadline = asyncio.get_event_loop().time() + _WS_PONG_TIMEOUT
            while asyncio.get_event_loop().time() < deadline and not _closed:
                if _pong_received_flag.is_set():
                    _pong_received_flag.clear()
                    pong_received = True
                    break
                await asyncio.sleep(0.1)
            if not pong_received and not _closed:
                _closed = True
                try:
                    await ws.close(code=1000, reason="idle_timeout")
                except (RuntimeError, OSError) as exc:
                    log.warning("ws_bars.ping_close_failed", error=str(exc))
                break

    # Flag to signal pong received from client
    _pong_received_flag = asyncio.Event()

    # Start background tasks
    listener_task: asyncio.Task[Any] = asyncio.create_task(_pubsub_listener())
    background_tasks.add(listener_task)
    ping_task: asyncio.Task[Any] = asyncio.create_task(_ping_loop())
    background_tasks.add(ping_task)

    async def _send_bars() -> None:
        """Drain bar_queue and forward envelopes to the client."""
        while not _closed:
            try:
                payload = await asyncio.wait_for(bar_queue.get(), timeout=0.2)
            except TimeoutError:
                continue

            # Per-message guard — codex C
            try:
                cid = payload.get("canonical_id", "")
                tf = payload.get("tf", payload.get("timeframe", ""))
                # Only forward if this connection is subscribed to this channel
                if (cid, tf) not in subscriptions:
                    continue

                revision = int(payload.get("revision", 0))
                partial = bool(payload.get("partial", True))
                if not partial:
                    revision = _FINAL_REVISION

                envelope = BarEnvelope(
                    canonical_id=cid,
                    timeframe=tf,
                    bucket_start=datetime.fromisoformat(payload["bucket_start"]),
                    open=str(payload.get("open") or "0"),
                    high=str(payload.get("high") or "0"),
                    low=str(payload.get("low") or "0"),
                    close=str(payload.get("close") or "0"),
                    volume=str(payload.get("volume") or "0"),
                    trade_count=int(payload.get("trade_count", 0)),
                    revision=revision,
                    partial=partial,
                )
                await ws.send_text(envelope.model_dump_json())
            except (KeyError, ValueError, TypeError, RuntimeError, OSError) as exc:
                log.warning("ws_bars.send.error", error=str(exc))

    send_task: asyncio.Task[Any] = asyncio.create_task(_send_bars())
    background_tasks.add(send_task)

    # ── Main client-message receive loop ────────────────────────────────────
    try:
        while not _closed:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except TimeoutError:
                continue
            except WebSocketDisconnect:
                break

            # Per-message guard — codex C
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError, ValueError:
                await ws.send_text(_make_error_frame("invalid_json"))
                continue

            op = frame.get("op", "")

            if op == "pong":
                _pong_received_flag.set()
                continue

            if op == "subscribe":
                cid = str(frame.get("canonical_id", ""))
                tf = str(frame.get("timeframe", ""))
                if not cid or not tf:
                    await ws.send_text(_make_error_frame("missing_field"))
                    continue
                # MED-27: validate canonical_id format and timeframe before subscribing
                if not (1 <= len(cid) <= 64) or not _CANONICAL_ID_RE.fullmatch(cid):
                    await ws.send_text(_make_error_frame("invalid_canonical_id"))
                    continue
                if tf not in _VALID_TIMEFRAMES:
                    await ws.send_text(_make_error_frame("invalid_timeframe"))
                    continue
                if len(subscriptions) >= max_subs and (cid, tf) not in subscriptions:
                    _closed = True
                    await ws.close(code=4029, reason="subscription_limit_exceeded")
                    break
                subscriptions.add((cid, tf))
                continue

            if op == "unsubscribe":
                cid = str(frame.get("canonical_id", ""))
                tf = str(frame.get("timeframe", ""))
                subscriptions.discard((cid, tf))
                continue

            await ws.send_text(_make_error_frame("unknown_op"))

    except WebSocketDisconnect:
        pass
    finally:
        _closed = True
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
