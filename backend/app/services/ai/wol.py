"""Phase 11a-A2 (HIGH-3 / HIGH-8): HeavyBoxWoL primitive.

BE on the VPS asks the NUC-side WoL helper to broadcast a magic
packet (the packet doesn't cross WG cleanly so the NUC, same LAN as
the heavy box, fires it). BE then polls heavy-box Ollama
``GET /api/tags`` until the requested model appears OR a deadline
elapses. Circuit breaker: 3 wake failures within 10min open the
breaker for 5min, after which one trial wake is allowed (half-open).

Idempotent under concurrent callers via asyncio.Event — multiple
requests for the same model share a single magic-packet + probe loop.
Single-replica today; multi-replica deferred to Phase 24.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.core.metrics import (
    AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE,
    AI_ROUTER_WOL_WAKE_FAILURES_TOTAL,
    AI_ROUTER_WOL_WAKE_LATENCY_SECONDS,
    AI_ROUTER_WOL_WAKE_TOTAL,
    AI_ROUTER_WOL_WARM_TO_READY_SECONDS,
)


@dataclass(frozen=True)
class WakeResult:
    status: Literal["ready", "failed", "circuit_open"]
    tcp_open_ms: int | None = None
    model_ready_ms: int | None = None
    error: str | None = None


@dataclass
class _BreakerState:
    failures: list[float] = field(default_factory=list)  # monotonic times
    opened_at: float | None = None


_FAILURE_WINDOW_S = 10 * 60
_FAILURE_THRESHOLD = 3
_OPEN_DURATION_S = 5 * 60


class HeavyBoxWoL:
    """Wakes the heavy box on demand and waits for a named Ollama model."""

    def __init__(
        self,
        *,
        helper_url: str,
        heavy_url: str,
        clock: Callable[[], float] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._helper_url = helper_url.rstrip("/")
        self._heavy_url = heavy_url.rstrip("/")
        self._heavy_host = self._heavy_url
        self._clock = clock or time.monotonic
        self.transport = transport  # public so tests can swap it mid-run
        self._poll_interval_s = poll_interval_s
        self._breaker = _BreakerState()
        # Per-model singleton wake — keyed by model name so different
        # models don't share each other's pending probe.
        self._inflight: dict[str, asyncio.Task[WakeResult]] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=10.0)

    def _circuit_state(self) -> Literal["closed", "open", "half_open"]:
        now = self._clock()
        if self._breaker.opened_at is None:
            return "closed"
        if (now - self._breaker.opened_at) < _OPEN_DURATION_S:
            return "open"
        return "half_open"

    def _record_failure(self) -> None:
        now = self._clock()
        # Drop failures outside the window.
        cutoff = now - _FAILURE_WINDOW_S
        self._breaker.failures = [t for t in self._breaker.failures if t >= cutoff]
        self._breaker.failures.append(now)
        if len(self._breaker.failures) >= _FAILURE_THRESHOLD:
            self._breaker.opened_at = now
            AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE.labels(host=self._heavy_host).set(2)

    def _record_success(self) -> None:
        self._breaker.failures.clear()
        self._breaker.opened_at = None
        AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE.labels(host=self._heavy_host).set(0)

    async def wake_and_wait_for_model(
        self, model_name: str, *, timeout_s: float = 60.0
    ) -> WakeResult:
        state = self._circuit_state()
        if state == "open":
            AI_ROUTER_WOL_WAKE_TOTAL.labels(
                host=self._heavy_host,
                outcome="circuit_open",
            ).inc()
            return WakeResult(status="circuit_open", error="breaker open after 3 failures")
        if state == "half_open":
            AI_ROUTER_WOL_CIRCUIT_BREAKER_STATE.labels(host=self._heavy_host).set(1)

        existing = self._inflight.get(model_name)
        if existing is not None and not existing.done():
            return await existing

        task = asyncio.create_task(self._do_wake(model_name, timeout_s))
        self._inflight[model_name] = task
        try:
            return await task
        finally:
            self._inflight.pop(model_name, None)

    async def _do_wake(self, model_name: str, timeout_s: float) -> WakeResult:
        started = self._clock()
        deadline = time.monotonic() + timeout_s
        async with self._client() as client:
            # 1) Tell the NUC helper to broadcast the magic packet.
            try:
                resp = await client.post(f"{self._helper_url}/wake")
            except Exception as exc:
                self._record_failure()
                AI_ROUTER_WOL_WAKE_TOTAL.labels(host=self._heavy_host, outcome="failed").inc()
                AI_ROUTER_WOL_WAKE_FAILURES_TOTAL.labels(
                    host=self._heavy_host,
                    reason="helper_error",
                ).inc()
                return WakeResult(status="failed", error=f"helper_unreachable: {exc}")
            if resp.status_code != 200:
                self._record_failure()
                AI_ROUTER_WOL_WAKE_TOTAL.labels(host=self._heavy_host, outcome="failed").inc()
                AI_ROUTER_WOL_WAKE_FAILURES_TOTAL.labels(
                    host=self._heavy_host,
                    reason="helper_error",
                ).inc()
                return WakeResult(
                    status="failed", error=f"helper_rejected: HTTP {resp.status_code}"
                )

            tcp_open_ms: int | None = None
            while time.monotonic() < deadline:
                try:
                    tags = await client.get(f"{self._heavy_url}/api/tags")
                except Exception:
                    await asyncio.sleep(max(self._poll_interval_s, 0.01))
                    continue
                if tcp_open_ms is None:
                    tcp_open_ms = int((self._clock() - started) * 1000)
                if tags.status_code == 200:
                    payload = tags.json()
                    names = {m.get("name") for m in payload.get("models", [])}
                    if model_name in names:
                        ready_elapsed = self._clock() - started
                        ready_ms = int(ready_elapsed * 1000)
                        self._record_success()
                        tcp_elapsed = tcp_open_ms / 1000
                        AI_ROUTER_WOL_WAKE_TOTAL.labels(
                            host=self._heavy_host,
                            outcome="ready",
                        ).inc()
                        AI_ROUTER_WOL_WAKE_LATENCY_SECONDS.labels(
                            host=self._heavy_host,
                        ).observe(tcp_elapsed)
                        AI_ROUTER_WOL_WARM_TO_READY_SECONDS.labels(
                            host=self._heavy_host,
                        ).observe(ready_elapsed)
                        return WakeResult(
                            status="ready",
                            tcp_open_ms=tcp_open_ms,
                            model_ready_ms=ready_ms,
                        )
                await asyncio.sleep(max(self._poll_interval_s, 0.01))

            self._record_failure()
            reason = "tcp_timeout" if tcp_open_ms is None else "model_timeout"
            AI_ROUTER_WOL_WAKE_TOTAL.labels(host=self._heavy_host, outcome="failed").inc()
            AI_ROUTER_WOL_WAKE_FAILURES_TOTAL.labels(
                host=self._heavy_host,
                reason=reason,
            ).inc()
            return WakeResult(
                status="failed",
                tcp_open_ms=tcp_open_ms,
                error="timeout_waiting_for_model",
            )
