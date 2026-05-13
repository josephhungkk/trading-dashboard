"""Thin facade around services/common/rate_limiter.SlidingWindowRateLimiter[K]."""

from __future__ import annotations

from app.services.common.rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)


def make_create_limiter() -> SlidingWindowRateLimiter[str]:
    return SlidingWindowRateLimiter(burst=5, window_seconds=60, name="alerts_create")


def make_dry_run_limiter() -> SlidingWindowRateLimiter[str]:
    return SlidingWindowRateLimiter(burst=10, window_seconds=60, name="alerts_dry_run")


__all__ = ["RateLimitExceededError", "make_create_limiter", "make_dry_run_limiter"]
