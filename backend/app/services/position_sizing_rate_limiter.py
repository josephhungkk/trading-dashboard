"""Phase 10b.1 H3 — in-process sliding-window rate limiter.

Thin facade over `services.common.rate_limiter.SlidingWindowRateLimiter[tuple[str, str]]`
(extracted in chunk 11a-B1, MED-3). Public surface preserved verbatim
including the two-positional-arg `.check(jwt_subject, account_id)` shape.

Single-replica today; multi-replica needs Redis backing (Phase 24).
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.common.rate_limiter import (
    RateLimitExceededError,
)
from app.services.common.rate_limiter import (
    SlidingWindowRateLimiter as _Generic,
)

__all__ = ["RateLimitExceededError", "SlidingWindowRateLimiter"]


class SlidingWindowRateLimiter(_Generic[tuple[str, str]]):
    """Per-(jwt_subject, account_id) limiter with the legacy 2-arg shape."""

    def __init__(
        self,
        *,
        burst: int,
        sustained_per_sec: int,
        window_seconds: int = 1,
        now: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(
            burst=burst,
            window_seconds=window_seconds,
            now=now,
            name="position_sizing",
        )

    def check(self, jwt_subject: str, account_id: str) -> None:  # type: ignore[override]
        super().check((jwt_subject, account_id))

    def evict_stale(self, jwt_subject: str, account_id: str) -> None:  # type: ignore[override]
        super().evict_stale((jwt_subject, account_id))
