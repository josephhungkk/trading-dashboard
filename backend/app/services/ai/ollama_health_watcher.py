"""Phase 11a-A2 Task 21-bis: Ollama health watcher."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

from app.core.metrics import (
    AI_ROUTER_OLLAMA_HEALTH_ALERT_PUBLISH_FAILURES_TOTAL,
    AI_ROUTER_OLLAMA_HEALTH_FAILURES_TOTAL,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_FAILURE_WINDOW_S = 10 * 60
_ALERT_THRESHOLD = 3
_PUBSUB_CHANNEL = "ai:ollama_health:alert"
OLLAMA_HEALTH_ALERT_CHANNEL = _PUBSUB_CHANNEL


@dataclass(frozen=True)
class HealthCheckResult:
    host: str
    healthy: bool
    error: str | None = None


@dataclass(frozen=True)
class OllamaHost:
    """Single Ollama endpoint watched by the BE process."""

    name: str
    base_url: str


@dataclass(frozen=True)
class OllamaHealthResult:
    """Detailed result from one host check."""

    host_name: str
    base_url: str
    healthy: bool
    error: str | None = None


class OllamaHealthWatcher:
    """Periodically poll each Ollama host; emit alerts on threshold breach.

    Alert state does NOT persist across restarts; expect up to 3 poll-cycles of
    silence after restart if backends are still down.
    """

    def __init__(
        self,
        *,
        hosts: Mapping[str, str] | Sequence[OllamaHost],
        redis: Redis,
        poll_seconds: float | None = None,
        poll_interval_s: float = 60.0,
        failure_threshold: int = _ALERT_THRESHOLD,
        failure_window_seconds: float = _FAILURE_WINDOW_S,
        request_timeout_seconds: float = 5.0,
        now: Callable[[], float] | None = None,
        clock: Callable[[], float] | None = None,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._hosts = (
            {host.name: host.base_url for host in hosts}
            if not isinstance(hosts, Mapping)
            else dict(hosts)
        )
        self._redis = redis
        self._poll_interval_s = poll_seconds if poll_seconds is not None else poll_interval_s
        self._failure_threshold = failure_threshold
        self._failure_window_s = failure_window_seconds
        self._request_timeout_s = request_timeout_seconds
        self._clock = now or clock or time.monotonic
        self._http_client = http_client
        self._transport = transport
        self._owns_http_client = False
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)
        self._alerted: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def check_once(
        self, host: OllamaHost | None = None
    ) -> list[HealthCheckResult] | OllamaHealthResult:
        """Run a single round of health checks, or check one host."""
        if host is not None:
            return await self._check_host(host)

        results: list[HealthCheckResult] = []
        client = self._http_client
        if client is not None:
            for host_name, base_url in self._hosts.items():
                result = await self._check_host(
                    OllamaHost(name=host_name, base_url=base_url),
                    client=client,
                )
                results.append(
                    HealthCheckResult(
                        host=result.host_name,
                        healthy=result.healthy,
                        error=result.error,
                    )
                )
            return results

        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._request_timeout_s
        ) as client:
            for host_name, base_url in self._hosts.items():
                result = await self._check_host(
                    OllamaHost(name=host_name, base_url=base_url),
                    client=client,
                )
                results.append(
                    HealthCheckResult(
                        host=result.host_name,
                        healthy=result.healthy,
                        error=result.error,
                    )
                )
        return results

    async def _check_host(
        self,
        host: OllamaHost,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> OllamaHealthResult:
        close_client = False
        if client is None:
            client = self._http_client
            if client is None:
                client = httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self._request_timeout_s,
                )
                close_client = True
        try:
            resp = await client.get(f"{host.base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200 and resp.json().get("models") is not None:
                return OllamaHealthResult(
                    host_name=host.name,
                    base_url=host.base_url,
                    healthy=True,
                )
            return OllamaHealthResult(
                host_name=host.name,
                base_url=host.base_url,
                healthy=False,
                error=f"unhealthy_status:{resp.status_code}",
            )
        except Exception as exc:
            return OllamaHealthResult(
                host_name=host.name,
                base_url=host.base_url,
                healthy=False,
                error=type(exc).__name__,
            )
        finally:
            if close_client:
                await client.aclose()

    async def _record(self, result: HealthCheckResult | OllamaHealthResult) -> None:
        host = result.host if isinstance(result, HealthCheckResult) else result.host_name
        if result.healthy:
            self._failures.pop(host, None)
            self._alerted.discard(host)
            return

        now = self._clock()
        cutoff = now - self._failure_window_s
        bucket = self._failures[host]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        AI_ROUTER_OLLAMA_HEALTH_FAILURES_TOTAL.labels(host=host).inc()

        if len(bucket) >= self._failure_threshold and host not in self._alerted:
            self._alerted.add(host)
            await self._emit_alert(result)

    async def _emit_alert(self, result: HealthCheckResult | OllamaHealthResult) -> None:
        host = result.host if isinstance(result, HealthCheckResult) else result.host_name
        log.error(
            "ollama_health_breach",
            host=host,
            error=result.error,
            threshold=self._failure_threshold,
            window_s=self._failure_window_s,
        )
        try:
            await self._redis.publish(
                _PUBSUB_CHANNEL,
                json.dumps(
                    {
                        "host": host,
                        "error": result.error or "",
                        "failures": self._failure_threshold,
                        "window_seconds": self._failure_window_s,
                    }
                ),
            )
        except Exception as exc:
            AI_ROUTER_OLLAMA_HEALTH_ALERT_PUBLISH_FAILURES_TOTAL.labels(host=host).inc()
            log.warning(
                "ollama_health_pubsub_failed",
                host=host,
                error_class=type(exc).__name__,
                error=str(exc),
            )

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                results = await self.check_once()
                if isinstance(results, list):
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
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._request_timeout_s,
            )
            self._owns_http_client = True
        self._task = asyncio.create_task(self._run())
        log.info(
            "watcher_started",
            hosts=list(self._hosts),
            poll_interval_s=self._poll_interval_s,
            failure_threshold=self._failure_threshold,
            failure_window_s=self._failure_window_s,
        )

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
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self._owns_http_client = False
