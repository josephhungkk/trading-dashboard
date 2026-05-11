"""Phase 10b.1 H3 — in-process sliding-window rate limiter.

Pattern mirrors backend/app/services/quotes/registry.py:144 (deque-based
sliding window). Per (jwt_subject, account_id) bucket. Single-replica
today; multi-replica will need Redis backing (Phase 24).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimitExceededError(Exception):
    """Raised when a (user, account) key exceeds its sliding-window quota."""


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter.

    Args:
        burst: Max requests inside the window.
        sustained_per_sec: Steady-state ceiling (reserved for future redis-backed impl).
        window_seconds: Window length.
        now: Time source; injected for tests.
    """

    def __init__(
        self,
        *,
        burst: int,
        sustained_per_sec: int,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._burst = burst
        self._sustained = sustained_per_sec
        self._window = window_seconds
        self._now = now or time.monotonic
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, jwt_subject: str, account_id: str) -> None:
        """Raises RateLimitExceededError if (subject, account) is over quota."""
        key = (jwt_subject, account_id)
        now = self._now()
        bucket = self._buckets[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._burst:
            raise RateLimitExceededError(
                f"position_sizing rate limit exceeded (burst={self._burst}, window={self._window}s)"
            )
        bucket.append(now)
