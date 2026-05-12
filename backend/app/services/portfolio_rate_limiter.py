"""Phase 10b.2 §5.2 — portfolio rollup rate limiter.

Architect HIGH #6: the existing position_sizing limiter at
backend/app/services/position_sizing_rate_limiter.py:43 keys on
``(jwt_subject, account_id)``. The portfolio rollup endpoints are
cross-account so that shape doesn't fit. Spin a fresh instance keyed on
``(jwt_subject, "portfolio")`` — a single shared bucket across all 3
rollup endpoints (rollup, curve, drill) so a curve fetch can't drown a
live rollup poll. Total cap: 10/s burst per jwt_subject.

Single-replica today; multi-replica will need Redis backing (Phase 24,
same constraint as position_sizing_rate_limiter).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class PortfolioRateLimitExceededError(Exception):
    """Raised when a jwt_subject exceeds its sliding-window quota."""


class PortfolioRateLimiter:
    """Per-jwt-subject sliding-window limiter.

    Args:
        burst: max requests inside the window.
        window_seconds: window length.
        now: time source; injected for tests.
    """

    def __init__(
        self,
        *,
        burst: int = 10,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._burst = burst
        self._window = window_seconds
        self._now = now or time.monotonic
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, jwt_subject: str) -> None:
        """Raise PortfolioRateLimitExceededError if subject is over quota."""
        if not jwt_subject:
            raise PortfolioRateLimitExceededError("portfolio rate limit: empty jwt_subject")
        now = self._now()
        bucket = self._buckets[jwt_subject]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._burst:
            raise PortfolioRateLimitExceededError(
                f"portfolio rate limit exceeded (burst={self._burst}, window={self._window}s)"
            )
        bucket.append(now)

    def evict_stale(self, jwt_subject: str) -> None:
        """Drop the bucket if empty after the cutoff sweep — prevents
        unbounded memory growth across unique jwt_subjects over time.

        Mirrors position_sizing_rate_limiter.evict_stale; caller-driven so
        the buckets dict doesn't accumulate idle subjects forever.
        """
        if jwt_subject not in self._buckets:
            return
        cutoff = self._now() - self._window
        bucket = self._buckets[jwt_subject]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket:
            del self._buckets[jwt_subject]


_LIMITER: PortfolioRateLimiter | None = None


def get_portfolio_limiter() -> PortfolioRateLimiter:
    """Module-level singleton accessor.

    Tests should call _reset_portfolio_limiter_for_tests() in an autouse
    fixture to prevent cross-test state leakage.
    """
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = PortfolioRateLimiter()
    return _LIMITER


def _reset_portfolio_limiter_for_tests() -> None:
    """Test-only: clear the module-level singleton."""
    global _LIMITER
    _LIMITER = None
