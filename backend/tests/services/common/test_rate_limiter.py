"""Tests for the generic SlidingWindowRateLimiter[K]."""

from __future__ import annotations

import pytest

from app.services.common.rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)

pytestmark = pytest.mark.no_db


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_check_under_burst_passes() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
        burst=3, window_seconds=1, now=clock
    )
    for _ in range(3):
        limiter.check("alice")


def test_check_at_burst_raises() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
        burst=2, window_seconds=1, now=clock
    )
    limiter.check("alice")
    limiter.check("alice")
    with pytest.raises(RateLimitExceededError):
        limiter.check("alice")


def test_window_expiry_admits_again() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
        burst=1, window_seconds=1, now=clock
    )
    limiter.check("alice")
    clock.t = 1.01
    limiter.check("alice")


def test_tuple_key_isolation() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[tuple[str, str]] = SlidingWindowRateLimiter(
        burst=1, window_seconds=1, now=clock
    )
    limiter.check(("alice", "acc1"))
    limiter.check(("alice", "acc2"))
    with pytest.raises(RateLimitExceededError):
        limiter.check(("alice", "acc1"))


def test_evict_stale_removes_idle_keys() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
        burst=1, window_seconds=1, now=clock
    )
    limiter.check("alice")
    clock.t = 1.5
    limiter.evict_stale("alice")
    assert "alice" not in limiter._buckets


def test_evict_stale_unknown_key_is_noop() -> None:
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(burst=1, window_seconds=1)
    limiter.evict_stale("never-checked")


def test_empty_string_key_rejected() -> None:
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(burst=1, window_seconds=1)
    with pytest.raises(RateLimitExceededError):
        limiter.check("")


def test_message_includes_burst_window_name() -> None:
    clock = _Clock()
    limiter: SlidingWindowRateLimiter[str] = SlidingWindowRateLimiter(
        burst=5, window_seconds=2, now=clock, name="position_sizing"
    )
    for _ in range(5):
        limiter.check("alice")
    with pytest.raises(RateLimitExceededError) as exc:
        limiter.check("alice")
    msg = str(exc.value)
    assert "burst=5" in msg
    assert "window=2s" in msg
    assert "position_sizing" in msg
