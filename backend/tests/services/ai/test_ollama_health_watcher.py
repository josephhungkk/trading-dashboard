"""Phase 11a-A2 Task 21-bis: Ollama health watcher tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.ai.ollama_health_watcher import HealthCheckResult, OllamaHealthWatcher

pytestmark = pytest.mark.no_db


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.mark.asyncio
async def test_check_once_healthy_when_tags_returns_200_with_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    redis = AsyncMock()
    watcher = OllamaHealthWatcher(
        hosts={"nuc": "http://ollama-nuc:11434"},
        redis=redis,
        transport=httpx.MockTransport(handler),
    )

    assert await watcher.check_once() == [HealthCheckResult(host="nuc", healthy=True)]


@pytest.mark.asyncio
async def test_check_once_unhealthy_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed", request=request)

    redis = AsyncMock()
    watcher = OllamaHealthWatcher(
        hosts={"nuc": "http://ollama-nuc:11434"},
        redis=redis,
        transport=httpx.MockTransport(handler),
    )

    assert await watcher.check_once() == [
        HealthCheckResult(host="nuc", healthy=False, error="ConnectError")
    ]


@pytest.mark.asyncio
async def test_check_once_unhealthy_on_500() -> None:
    redis = AsyncMock()
    watcher = OllamaHealthWatcher(
        hosts={"nuc": "http://ollama-nuc:11434"},
        redis=redis,
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, json={})),
    )

    assert await watcher.check_once() == [
        HealthCheckResult(host="nuc", healthy=False, error="unhealthy_status:500")
    ]


@pytest.mark.asyncio
async def test_alert_fires_after_3_failures_in_window() -> None:
    redis = AsyncMock()
    clock = FakeClock()
    watcher = OllamaHealthWatcher(hosts={}, redis=redis, clock=clock)
    failure = HealthCheckResult(host="nuc", healthy=False, error="ConnectError")

    await watcher._record(failure)
    redis.publish.assert_not_awaited()
    clock.advance(60)

    await watcher._record(failure)
    redis.publish.assert_not_awaited()
    clock.advance(60)

    await watcher._record(failure)

    redis.publish.assert_awaited_once()
    assert redis.publish.await_args.args[0] == "ai:ollama_health:alert"


@pytest.mark.asyncio
async def test_alert_does_not_re_fire_while_still_unhealthy() -> None:
    redis = AsyncMock()
    clock = FakeClock()
    watcher = OllamaHealthWatcher(hosts={}, redis=redis, clock=clock)
    failure = HealthCheckResult(host="nuc", healthy=False, error="ConnectError")

    for _ in range(6):
        await watcher._record(failure)
        clock.advance(60)

    assert redis.publish.await_count == 1


@pytest.mark.asyncio
async def test_alert_re_fires_after_recovery_then_new_breach() -> None:
    redis = AsyncMock()
    clock = FakeClock()
    watcher = OllamaHealthWatcher(hosts={}, redis=redis, clock=clock)
    failure = HealthCheckResult(host="nuc", healthy=False, error="ConnectError")
    success = HealthCheckResult(host="nuc", healthy=True)

    for _ in range(3):
        await watcher._record(failure)
        clock.advance(60)

    await watcher._record(success)
    clock.advance(60)

    for _ in range(3):
        await watcher._record(failure)
        clock.advance(60)

    assert redis.publish.await_count == 2
