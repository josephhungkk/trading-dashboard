"""SidecarStream — bidi gRPC StreamQuotes client per source (HIGH-1).

Phase 7b.1. One :class:`SidecarStream` per registered source (ibkr, futu,
schwab, …). Owns:

* the persistent gRPC bidi stream,
* the reconnect/backoff loop,
* the **Subscribe vs Resync** first-frame decision (HIGH-1):
    * Sidecar process restart → ``HealthResponse.started_at`` changes →
      first frame is **Subscribe** with the full active set (sidecar lost
      its broker-side refcount and must re-issue ``reqMktData``).
    * gRPC channel only reconnected (sidecar process unchanged) → first
      frame is **Resync** with the same active set; sidecar reconciles its
      existing refcounts against ``expected`` and only diffs propagate to
      the broker socket. Avoids duplicate IBKR ``reqMktData`` calls (which
      are NOT idempotent).
* token-rotation triggered reconnect (CRIT-2 hook).

Callers feed runtime sub/unsub diffs in via :meth:`add` / :meth:`remove`
which enqueue ``StreamQuotesRequest`` frames; the run loop drains the
queue (with a 30-s heartbeat as a keep-alive). Per-tick callback fires
``on_quote(QuoteMessage)`` and bumps :class:`SourceHealthMap` last-tick.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

import grpc  # type: ignore[import-untyped]
import structlog
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from app._generated.broker.v1 import broker_pb2 as pb
from app._generated.broker.v1 import broker_pb2_grpc as pb_grpc
from app.core.metrics import (
    QUOTE_SIDECAR_FIRST_FRAME_TOTAL,
    QUOTE_SIDECAR_RECONNECT_TOTAL,
)
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceHealthState

_HEARTBEAT_INTERVAL_SECONDS: float = 30.0
_BACKOFF_INITIAL_SECONDS: float = 1.0
_BACKOFF_MAX_SECONDS: float = 60.0

OnQuote = Callable[[pb.QuoteMessage], Awaitable[None]]
SymbolRefBuilder = Callable[[str], pb.SymbolRef]

_log = structlog.get_logger(__name__)


class SidecarStream:
    """Persistent bidi-gRPC StreamQuotes client for one source."""

    def __init__(
        self,
        *,
        source: str,
        channel: grpc.aio.Channel,
        registry: SubscriptionRegistry,
        on_quote: OnQuote,
        health: SourceHealthMap,
        symbol_ref_builder: SymbolRefBuilder,
    ) -> None:
        self._source = source
        self._channel = channel
        self._registry = registry
        self._on_quote = on_quote
        self._health = health
        self._build_symbol_ref = symbol_ref_builder

        self._pending: asyncio.Queue[pb.StreamQuotesRequest] = asyncio.Queue()
        self._last_known_started_at: int | None = None
        self._stopping = asyncio.Event()
        self._token_rotation = asyncio.Event()

    # ── public API ────────────────────────────────────────────────────────

    async def add(self, canonical_ids: Iterable[str]) -> None:
        """Enqueue a Subscribe frame for ``canonical_ids``. Caller has
        already updated the registry; this only propagates to the sidecar."""
        symbols = [self._build_symbol_ref(c) for c in canonical_ids]
        if not symbols:
            return
        await self._pending.put(
            pb.StreamQuotesRequest(subscribe=pb.StreamQuotesRequest.Subscribe(symbols=symbols))
        )

    async def remove(self, canonical_ids: Iterable[str]) -> None:
        symbols = [self._build_symbol_ref(c) for c in canonical_ids]
        if not symbols:
            return
        await self._pending.put(
            pb.StreamQuotesRequest(unsubscribe=pb.StreamQuotesRequest.Unsubscribe(symbols=symbols))
        )

    def request_reconnect(self) -> None:
        """Signal the run loop to drop the current bidi stream and reconnect.
        Used by the CRIT-2 token-rotation hook (Schwab refresh-token rolls).
        """
        self._token_rotation.set()

    def stop(self) -> None:
        """Tell the run loop to exit on the next iteration boundary."""
        self._stopping.set()
        self._token_rotation.set()  # unblocks any wait_for sleep

    # ── run loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        backoff = _BACKOFF_INITIAL_SECONDS
        while not self._stopping.is_set():
            reason: str | None = None
            try:
                await self._one_round()
                backoff = _BACKOFF_INITIAL_SECONDS

                # Stop preempts everything — including a token-rotation event
                # that stop() also sets to unblock the inner wait. Without
                # this ordering, calling stop() would spuriously bump
                # reconnect_total{reason=token_rotation}.
                if self._stopping.is_set():
                    return

                if self._token_rotation.is_set():
                    self._token_rotation.clear()
                    reason = "token_rotation"
                else:
                    reason = "idle_timeout"
            except grpc.aio.AioRpcError as e:
                _log.warning(
                    "sidecar_stream.aio_rpc_error",
                    source=self._source,
                    code=e.code(),
                )
                self._health.set_state(self._source, SourceHealthState.DOWN)
                reason = "aio_rpc_error"
            except (ConnectionError, OSError) as e:
                _log.warning(
                    "sidecar_stream.connection_error",
                    source=self._source,
                    error=repr(e),
                )
                self._health.set_state(self._source, SourceHealthState.DOWN)
                reason = "connection_error"
            except Exception:
                # Surprise — log + count + keep the loop alive so the engine
                # can recover. Re-raising would tear down the per-source
                # task and leave the source permanently dead.
                _log.exception(
                    "sidecar_stream.unexpected_error",
                    source=self._source,
                )
                self._health.set_state(self._source, SourceHealthState.DOWN)
                reason = "unexpected"

            QUOTE_SIDECAR_RECONNECT_TOTAL.labels(source=self._source, reason=reason).inc()

            if self._stopping.is_set():
                return

            # Token-rotation reconnects without backoff (CRIT-2 hot path).
            if reason != "token_rotation":
                await asyncio.sleep(min(backoff, _BACKOFF_MAX_SECONDS))
                backoff = min(backoff * 2.0, _BACKOFF_MAX_SECONDS)

    async def _one_round(self) -> None:
        """Single connect → first-frame → drain ticks pass."""
        stub = pb_grpc.BrokerStub(self._channel)

        health_resp = await stub.Health(pb.HealthRequest())
        current_started_at = self._extract_started_at(health_resp)

        first_frame_kind = self._decide_first_frame(current_started_at)
        QUOTE_SIDECAR_FIRST_FRAME_TOTAL.labels(source=self._source, kind=first_frame_kind).inc()

        self._last_known_started_at = current_started_at

        # Mark UP — fail-closed routing now permits the source.
        self._health.set_state(self._source, SourceHealthState.HEALTHY)

        async def request_iter() -> AsyncIterator[pb.StreamQuotesRequest]:
            yield self._build_first_frame(first_frame_kind)
            while not self._stopping.is_set() and not self._token_rotation.is_set():
                # Race the pending-queue against stop/rotation so a token
                # rotation aborts within the asyncio scheduler's next tick
                # rather than waiting up to _HEARTBEAT_INTERVAL_SECONDS.
                get_task = asyncio.create_task(self._pending.get())
                stop_task = asyncio.create_task(self._stopping.wait())
                rot_task = asyncio.create_task(self._token_rotation.wait())
                try:
                    done, _ = await asyncio.wait(
                        {get_task, stop_task, rot_task},
                        timeout=_HEARTBEAT_INTERVAL_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    # Cancel-and-await the losers so their coroutine frames
                    # release immediately (bare cancel() leaks until GC).
                    for t in (get_task, stop_task, rot_task):
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(
                        get_task,
                        stop_task,
                        rot_task,
                        return_exceptions=True,
                    )
                if get_task in done and not get_task.cancelled():
                    yield get_task.result()
                    continue
                if stop_task in done or rot_task in done:
                    return
                # Plain timeout — emit heartbeat.
                yield self._build_heartbeat()

        async for resp in stub.StreamQuotes(request_iter()):
            self._health.update_last_tick(self._source, time.monotonic())
            await self._on_quote(resp)
            if self._token_rotation.is_set() or self._stopping.is_set():
                break

    # ── helpers ──────────────────────────────────────────────────────────

    def _decide_first_frame(self, current_started_at: int) -> str:
        """``subscribe`` if cold start or sidecar restarted; ``resync`` if
        the sidecar process has not restarted (gRPC channel-only reconnect).
        """
        if self._last_known_started_at is None or current_started_at != self._last_known_started_at:
            return "subscribe"
        return "resync"

    def _build_first_frame(self, kind: str) -> pb.StreamQuotesRequest:
        active = list(self._registry.get_active_for(self._source))
        symbols = [self._build_symbol_ref(c) for c in active]
        if kind == "subscribe":
            return pb.StreamQuotesRequest(
                subscribe=pb.StreamQuotesRequest.Subscribe(symbols=symbols)
            )
        return pb.StreamQuotesRequest(resync=pb.StreamQuotesRequest.Resync(expected=symbols))

    def _build_heartbeat(self) -> pb.StreamQuotesRequest:
        ts = Timestamp()
        ts.GetCurrentTime()
        return pb.StreamQuotesRequest(
            heartbeat=pb.StreamQuotesRequest.Heartbeat(
                client_time=ts,
                tick_count_received=0,
            )
        )

    @staticmethod
    def _extract_started_at(health_resp: Any) -> int:
        """Pull the unix-seconds value from ``HealthResponse.started_at``.

        Defends against an unset Timestamp (which equals 0/0) by treating
        a zero ``seconds`` as ``0`` — the cold-start signal that forces
        the first frame to ``subscribe``.
        """
        ts = getattr(health_resp, "started_at", None)
        if ts is None:
            return 0
        seconds = getattr(ts, "seconds", 0)
        return int(seconds)
