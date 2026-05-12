"""Phase 10b.2 §5.2 — PortfolioRateLimiter unit tests."""

from __future__ import annotations

import pytest

from app.services.portfolio_rate_limiter import (
    PortfolioRateLimiter,
    PortfolioRateLimitExceededError,
)


def test_burst_cap_blocks_over_quota() -> None:
    """N+1 calls within the window for the same subject raise."""
    t = [0.0]
    lim = PortfolioRateLimiter(burst=3, window_seconds=1, now=lambda: t[0])
    for _ in range(3):
        lim.check("user-a")
    with pytest.raises(PortfolioRateLimitExceededError):
        lim.check("user-a")


def test_window_expiry_releases_quota() -> None:
    """Entries older than the window get popped."""
    t = [0.0]
    lim = PortfolioRateLimiter(burst=2, window_seconds=1, now=lambda: t[0])
    lim.check("user-a")
    lim.check("user-a")
    # Slide forward past the window — old entries drop, new one fits.
    t[0] = 1.5
    lim.check("user-a")


def test_separate_buckets_per_subject() -> None:
    """Different jwt_subjects don't share quota."""
    t = [0.0]
    lim = PortfolioRateLimiter(burst=1, window_seconds=1, now=lambda: t[0])
    lim.check("user-a")
    lim.check("user-b")  # different subject; own bucket
    with pytest.raises(PortfolioRateLimitExceededError):
        lim.check("user-a")
