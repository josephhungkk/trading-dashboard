"""Phase 11a HIGH-9 — AI router rate limiter (per-subject sliding window +
per-capability semaphore)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog

from app.core import metrics
from app.services.common.rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)

log = structlog.get_logger(__name__)

_DEFAULT_SEMAPHORE_KEY = "__default__"


class AIRouterRateLimiter:
    def __init__(
        self,
        *,
        per_subject_burst: int = 30,
        per_subject_window_s: int = 60,
        semaphores: dict[str, int],
        now: Callable[[], float] | None = None,
    ) -> None:
        if _DEFAULT_SEMAPHORE_KEY not in semaphores:
            raise ValueError(f"semaphores must contain a {_DEFAULT_SEMAPHORE_KEY!r} fallback")
        self._sliding: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
            burst=per_subject_burst,
            window_seconds=per_subject_window_s,
            now=now,
            name="ai_router",
        )
        self._sems = {k: asyncio.Semaphore(v) for k, v in semaphores.items()}

    @asynccontextmanager
    async def check_and_acquire(self, jwt_subject: str, capability: str) -> AsyncIterator[None]:
        try:
            self._sliding.check(jwt_subject)
        except RateLimitExceededError:
            metrics.AI_ROUTER_RATE_LIMITED_TOTAL.labels(capability=capability).inc()
            raise
        sem = self._sems.get(capability) or self._sems[_DEFAULT_SEMAPHORE_KEY]
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def evict_stale(self, jwt_subject: str) -> None:
        self._sliding.evict_stale(jwt_subject)
