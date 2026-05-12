"""Phase 10b.2 §5.2 — portfolio rollup rate limiter.

Thin facade over `services.common.rate_limiter.SlidingWindowRateLimiter[str]`
(extracted in chunk 11a-B1, MED-3). Public surface preserved verbatim.

Single-replica today; multi-replica will need Redis backing (Phase 24,
same constraint as position_sizing_rate_limiter).
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.common.rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)


class PortfolioRateLimitExceededError(RateLimitExceededError):
    """Raised when a jwt_subject exceeds its sliding-window quota."""


class PortfolioRateLimiter(SlidingWindowRateLimiter[str]):
    """Per-jwt-subject sliding-window limiter (str key)."""

    def __init__(
        self,
        *,
        burst: int = 10,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(
            burst=burst,
            window_seconds=window_seconds,
            now=now,
            name="portfolio",
        )

    def check(self, jwt_subject: str) -> None:
        try:
            super().check(jwt_subject)
        except RateLimitExceededError as exc:
            raise PortfolioRateLimitExceededError(str(exc)) from None


_LIMITER: PortfolioRateLimiter | None = None


def get_portfolio_limiter() -> PortfolioRateLimiter:
    """Module-level singleton accessor."""
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = PortfolioRateLimiter()
    return _LIMITER


def _reset_portfolio_limiter_for_tests() -> None:
    """Test-only: clear the module-level singleton."""
    global _LIMITER
    _LIMITER = None
