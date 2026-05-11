"""Phase 10a B4 — Redis in-flight counter unit tests.

Spec: ``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md`` §1 #4 + §10.
Plan: ``docs/superpowers/plans/2026-05-08-phase10a-risk-engine-plan.md`` Task B4.

Counters live in Redis under ``risk:pdt:{account_id}`` and
``risk:bp_committed:{account_id}``. They close the broker-staleness window
between submit and broker ACK by predicting consumption optimistically;
``revert_*`` rolls back on rejection; ``reconcile_*`` overwrites with the
authoritative broker-reported value (TTL=120s). Single-replica today
(see Phase 10a F1 invariant note); Phase 24 introduces multi-worker
locking before scaling out.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


@pytest.fixture
def account_id() -> uuid.UUID:
    return uuid.UUID("11111111-2222-3333-4444-555555555555")


# ─── PDT counter ────────────────────────────────────────────────────────


async def test_decrement_pdt_returns_post_decrement_int(account_id: uuid.UUID) -> None:
    """``decrement_pdt`` calls ``redis.decr(key)`` and returns the new int."""
    from app.services.risk_inflight_counters import decrement_pdt

    redis = AsyncMock()
    redis.decr = AsyncMock(return_value=2)
    got = await decrement_pdt(redis, account_id)
    assert got == 2
    redis.decr.assert_awaited_once_with(f"risk:pdt:{account_id}")


async def test_revert_pdt_returns_post_increment_int(account_id: uuid.UUID) -> None:
    """``revert_pdt`` calls ``redis.incr(key)`` and returns the new int."""
    from app.services.risk_inflight_counters import revert_pdt

    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=3)
    got = await revert_pdt(redis, account_id)
    assert got == 3
    redis.incr.assert_awaited_once_with(f"risk:pdt:{account_id}")


async def test_inflight_pdt_remaining_returns_int_when_set(account_id: uuid.UUID) -> None:
    """Counter present in Redis → return ``int(value)``."""
    from app.services.risk_inflight_counters import inflight_pdt_remaining

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2")
    got = await inflight_pdt_remaining(redis, account_id)
    assert got == 2
    redis.get.assert_awaited_once_with(f"risk:pdt:{account_id}")


async def test_inflight_pdt_remaining_returns_none_when_unset(account_id: uuid.UUID) -> None:
    """Key absent → ``None`` so caller can fall back to broker-reported value."""
    from app.services.risk_inflight_counters import inflight_pdt_remaining

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    got = await inflight_pdt_remaining(redis, account_id)
    assert got is None


async def test_reconcile_pdt_writes_with_120s_ttl(account_id: uuid.UUID) -> None:
    """``reconcile_pdt`` SETs the broker-reported value with 120s TTL."""
    from app.services.risk_inflight_counters import reconcile_pdt

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    await reconcile_pdt(redis, account_id, broker_reported=4)
    redis.set.assert_awaited_once_with(f"risk:pdt:{account_id}", "4", ex=120)
