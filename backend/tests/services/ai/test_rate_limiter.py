from __future__ import annotations

import asyncio

import pytest

from app.core import metrics
from app.services.ai.rate_limiter import AIRouterRateLimiter
from app.services.common.rate_limiter import RateLimitExceededError

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_per_subject_sliding_window() -> None:
    now = 1_000.0
    limiter = AIRouterRateLimiter(
        per_subject_burst=2,
        per_subject_window_s=60,
        semaphores={"CLASSIFY": 1, "__default__": 1},
        now=lambda: now,
    )

    async with limiter.check_and_acquire("subject-a", "CLASSIFY"):
        pass
    async with limiter.check_and_acquire("subject-a", "CLASSIFY"):
        pass

    with pytest.raises(RateLimitExceededError):
        async with limiter.check_and_acquire("subject-a", "CLASSIFY"):
            pass

    assert metrics.AI_ROUTER_RATE_LIMITED_TOTAL.labels(capability="CLASSIFY")._value.get() == 1


@pytest.mark.asyncio
async def test_per_capability_semaphore_limits_concurrent() -> None:
    limiter = AIRouterRateLimiter(
        per_subject_burst=10,
        semaphores={"REASONING": 2, "__default__": 1},
    )
    release = asyncio.Event()
    entered_event = asyncio.Event()
    entered: list[str] = []

    async def hold(subject: str) -> None:
        async with limiter.check_and_acquire(subject, "REASONING"):
            entered.append(subject)
            if len(entered) == 2:
                entered_event.set()
            await release.wait()

    first = asyncio.create_task(hold("subject-1"))
    second = asyncio.create_task(hold("subject-2"))
    await entered_event.wait()

    third = asyncio.create_task(hold("subject-3"))
    await asyncio.sleep(0)
    assert entered == ["subject-1", "subject-2"]

    release.set()
    await asyncio.gather(first, second, third)
    assert entered == ["subject-1", "subject-2", "subject-3"]


@pytest.mark.asyncio
async def test_semaphore_released_on_exception() -> None:
    limiter = AIRouterRateLimiter(
        per_subject_burst=10,
        semaphores={"REASONING": 1, "__default__": 1},
    )

    with pytest.raises(RuntimeError, match="boom"):
        async with limiter.check_and_acquire("subject-a", "REASONING"):
            raise RuntimeError("boom")

    async with asyncio.timeout(0.1):
        async with limiter.check_and_acquire("subject-b", "REASONING"):
            pass


@pytest.mark.asyncio
async def test_unknown_capability_uses_default_semaphore() -> None:
    limiter = AIRouterRateLimiter(
        per_subject_burst=10,
        semaphores={"REASONING": 2, "__default__": 1},
    )
    release = asyncio.Event()
    first_entered = asyncio.Event()
    entered: list[str] = []

    async def hold(subject: str, capability: str) -> None:
        async with limiter.check_and_acquire(subject, capability):
            entered.append(capability)
            first_entered.set()
            await release.wait()

    first = asyncio.create_task(hold("subject-1", "UNKNOWN_A"))
    await first_entered.wait()

    second = asyncio.create_task(hold("subject-2", "UNKNOWN_B"))
    await asyncio.sleep(0)
    assert entered == ["UNKNOWN_A"]

    release.set()
    await asyncio.gather(first, second)
    assert entered == ["UNKNOWN_A", "UNKNOWN_B"]


@pytest.mark.asyncio
async def test_metrics_labels_match_spec() -> None:
    metrics.AI_ROUTER_RATE_LIMITED_TOTAL.labels(capability="REASONING")._value.set(0)
    limiter = AIRouterRateLimiter(
        per_subject_burst=1,
        per_subject_window_s=60,
        semaphores={"REASONING": 1, "__default__": 1},
        now=lambda: 1_000.0,
    )

    async with limiter.check_and_acquire("subject-a", "REASONING"):
        pass

    with pytest.raises(RateLimitExceededError):
        async with limiter.check_and_acquire("subject-a", "REASONING"):
            pass

    assert metrics.AI_ROUTER_RATE_LIMITED_TOTAL.labels(capability="REASONING")._value.get() == 1
