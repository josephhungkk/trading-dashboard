"""Generic per-key sliding-window rate limiter (MED-3 — extracted from
portfolio_rate_limiter.py + position_sizing_rate_limiter.py).

Key type is generic so callers can use ``str``, ``tuple[str, str]``, or
anything hashable. Single-replica today; multi-replica needs Redis backing
(Phase 24, same constraint as the originals).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable


class RateLimitExceededError(Exception):
    """Raised when a key exceeds its sliding-window quota."""


class SlidingWindowRateLimiter[K: Hashable]:
    """Per-key sliding-window limiter.

    Args:
        burst: Max requests inside the window.
        window_seconds: Window length.
        now: Time source; injected for tests.
        name: Optional label for the exception message (e.g. ``"position_sizing"``).
    """

    def __init__(
        self,
        *,
        burst: int,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
        name: str = "rate",
    ) -> None:
        self._burst = burst
        self._window = window_seconds
        self._now = now or time.monotonic
        self._name = name
        self._buckets: dict[K, deque[float]] = defaultdict(deque)

    def check(self, key: K) -> None:
        """Raise RateLimitExceededError if ``key`` is over quota."""
        if isinstance(key, str) and not key:
            raise RateLimitExceededError(f"{self._name} rate limit: empty key")
        now = self._now()
        bucket = self._buckets[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._burst:
            raise RateLimitExceededError(
                f"{self._name} rate limit exceeded (burst={self._burst}, window={self._window}s)"
            )
        bucket.append(now)

    def evict_stale(self, key: K) -> None:
        """Drop the bucket if empty after the cutoff sweep — caller-driven
        eviction to bound memory across unique keys over time."""
        if key not in self._buckets:
            return
        cutoff = self._now() - self._window
        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket:
            del self._buckets[key]
