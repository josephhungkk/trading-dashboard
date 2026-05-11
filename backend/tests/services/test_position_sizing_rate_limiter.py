from __future__ import annotations

import time

import pytest

from app.services.position_sizing_rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)


def test_allows_under_burst() -> None:
    limiter = SlidingWindowRateLimiter(burst=5, sustained_per_sec=2, window_seconds=1)
    for _ in range(5):
        limiter.check("user-A", "account-1")


def test_rejects_at_burst_plus_one() -> None:
    limiter = SlidingWindowRateLimiter(burst=5, sustained_per_sec=2, window_seconds=1)
    for _ in range(5):
        limiter.check("user-A", "account-1")
    with pytest.raises(RateLimitExceededError):
        limiter.check("user-A", "account-1")


def test_isolates_per_user_account_key() -> None:
    limiter = SlidingWindowRateLimiter(burst=3, sustained_per_sec=2, window_seconds=1)
    for _ in range(3):
        limiter.check("user-A", "account-1")
    # Different user OR account → its own bucket
    limiter.check("user-B", "account-1")  # ok
    limiter.check("user-A", "account-2")  # ok


def test_window_expires() -> None:
    """Inject a fake clock so the test doesn't sleep."""
    now_holder = [1000.0]

    def fake_now() -> float:
        return now_holder[0]

    limiter = SlidingWindowRateLimiter(
        burst=3, sustained_per_sec=10, window_seconds=1, now=fake_now
    )
    for _ in range(3):
        limiter.check("user-A", "account-1")
    with pytest.raises(RateLimitExceededError):
        limiter.check("user-A", "account-1")
    now_holder[0] = 1001.2  # advance past the 1s window
    limiter.check("user-A", "account-1")  # window expired → allowed again


def test_window_expires_real_clock() -> None:
    """Smoke-test against the real monotonic clock (slow path)."""
    limiter = SlidingWindowRateLimiter(burst=3, sustained_per_sec=10, window_seconds=1)
    for _ in range(3):
        limiter.check("user-A", "account-1")
    time.sleep(1.1)
    limiter.check("user-A", "account-1")
