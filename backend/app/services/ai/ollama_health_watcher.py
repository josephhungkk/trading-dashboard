"""Phase 11a-A2 Task 21-bis: Ollama health watcher."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

# Thresholds matching the WoL circuit breaker (10b.2 + 11a-A2 consistency).
_FAILURE_WINDOW_S = 10 * 60
_ALERT_THRESHOLD = 3
_PUBSUB_CHANNEL = "ai:ollama_health:alert"


@dataclass(frozen=True)
class HealthCheckResult:
    host: str
    healthy: bool
    error: str | None = None


class OllamaHealthWatcher:
    """Periodically poll each Ollama host; emit alerts on threshold breach.

    Args:
        hosts: mapping of host_name -> base URL (e.g. {'nuc': 'http://10.10.0.2:11434'}).
          Heavy-box typically NOT in this map — its sleep state would flap the watcher;
          its health is implicit in WoL wake success.
        redis: redis.asyncio client for pubsub alert events.
        poll_interval_s: time between rounds of health checks.
        clock: time source for tests.
        transport: httpx transport injection for tests.
    """

    def __init__(
        self,
        *,
        hosts: dict[str, str],
        redis: Redis,
        poll_interval_s: float = 60.0,
        clock: Callable[[], float] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._hosts = dict(hosts)
        self._redis = redis
        self._poll_interval_s = poll_interval_s
        self._clock = clock or time.monotonic
        self._transport = transport
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)
        self._alerted: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def check_once(self) -> list[HealthCheckResult]:
        """Run a single round of health checks."""
        results: list[HealthCheckResult] = []
        async with httpx.AsyncClient(transport=self._transport, timeout=5.0) as client:
            for host, base_url in self._hosts.items():
                try:
                    resp = await client.get(f"{base_url}/api/tags")
                    if resp.status_code == 200 and resp.json().get("models") is not None:
                        results.append(HealthCheckResult(host=host, healthy=True))
                    else:
                        results.append(
                            HealthCheckResult(
                                host=host,
                                healthy=False,
                                error=f"unhealthy_status:{resp.status_code}",
                            )
                        )
                except Exception as exc:
                    results.append(
                        HealthCheckResult(host=host, healthy=False, error=type(exc).__name__)
                    )
        return results

    async def _record(self, result: HealthCheckResult) -> None:
        now = self._clock()
        cutoff = now - _FAILURE_WINDOW_S
        bucket = self._failures[result.host]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if result.healthy:
            self._alerted.discard(result.host)
            return
        bucket.append(now)
        if len(bucket) >= _ALERT_THRESHOLD and result.host not in self._alerted:
            self._alerted.add(result.host)
            await self._emit_alert(result)

    async def _emit_alert(self, result: HealthCheckResult) -> None:
        log.error(
            "ollama_health_breach",
            host=result.host,
            error=result.error,
            threshold=_ALERT_THRESHOLD,
            window_s=_FAILURE_WINDOW_S,
        )
        try:
            await self._redis.publish(
                _PUBSUB_CHANNEL,
                f'{{"host":"{result.host}","error":"{result.error or ""}"}}',
            )
        except Exception as exc:
            log.warning(
                "ollama_health_pubsub_failed",
                error_class=type(exc).__name__,
                error=str(exc),
            )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                results = await self.check_once()
                for result in results:
                    await self._record(result)
            except Exception as exc:
                log.warning(
                    "ollama_health_check_loop_error",
                    error_class=type(exc).__name__,
                    error=str(exc),
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except TimeoutError:
                pass

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
