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
from decimal import Decimal
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


# ─── B9 reviewer findings: PDT cold-cache SETNX + BP counter tests ──────


async def test_decrement_pdt_seeds_cold_cache_via_setnx(account_id: uuid.UUID) -> None:
    """Cold cache + broker_reported -> SET NX EX 86400 then DECR.

    Without the SETNX seed, Redis DECR auto-initialises the key to 0 then
    decrements to -1, which would falsely BLOCK every trade until reconcile.
    """
    from app.services.risk_inflight_counters import _PDT_TTL_SEC, decrement_pdt

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=2)
    got = await decrement_pdt(redis, account_id, broker_reported=3)
    assert got == 2
    redis.set.assert_awaited_once_with(f"risk:pdt:{account_id}", "3", ex=_PDT_TTL_SEC, nx=True)
    redis.decr.assert_awaited_once_with(f"risk:pdt:{account_id}")


async def test_decrement_pdt_without_broker_reported_skips_seed(account_id: uuid.UUID) -> None:
    """No broker_reported -> no SETNX (caller is responsible for reconcile-first)."""
    from app.services.risk_inflight_counters import decrement_pdt

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=-1)
    got = await decrement_pdt(redis, account_id)
    assert got == -1
    redis.set.assert_not_awaited()


# ─── BP counter tests (B9 finding: zero coverage on commit_bp/revert_bp/etc.)


async def test_commit_bp_returns_decimal_post_commit(account_id: uuid.UUID) -> None:
    """``commit_bp`` adds notional via INCRBYFLOAT and returns Decimal."""

    from app.services.risk_inflight_counters import commit_bp

    redis = AsyncMock()
    redis.incrbyfloat = AsyncMock(return_value=15000.0)
    got = await commit_bp(redis, account_id, Decimal("15000"))
    assert got == Decimal("15000")
    assert isinstance(got, Decimal)
    redis.incrbyfloat.assert_awaited_once_with(f"risk:bp_committed:{account_id}", 15000.0)


async def test_revert_bp_subtracts_via_negative_incrbyfloat(account_id: uuid.UUID) -> None:
    """``revert_bp`` passes -notional to INCRBYFLOAT (subtraction idiom)."""

    from app.services.risk_inflight_counters import revert_bp

    redis = AsyncMock()
    redis.incrbyfloat = AsyncMock(return_value=0.0)
    got = await revert_bp(redis, account_id, Decimal("15000"))
    assert got == Decimal("0")
    assert isinstance(got, Decimal)
    # Critical: the wire arg must be NEGATIVE.
    redis.incrbyfloat.assert_awaited_once_with(f"risk:bp_committed:{account_id}", -15000.0)


async def test_inflight_bp_committed_returns_decimal_zero_when_unset(
    account_id: uuid.UUID,
) -> None:
    """Cold cache -> Decimal('0') (not None, not float 0.0)."""

    from app.services.risk_inflight_counters import inflight_bp_committed

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    got = await inflight_bp_committed(redis, account_id)
    assert got == Decimal("0")
    assert isinstance(got, Decimal)


async def test_inflight_bp_committed_returns_decimal_when_set(account_id: uuid.UUID) -> None:
    """Set value -> Decimal(stringified value)."""

    from app.services.risk_inflight_counters import inflight_bp_committed

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="40000.50")
    got = await inflight_bp_committed(redis, account_id)
    assert got == Decimal("40000.50")
    assert isinstance(got, Decimal)


async def test_reconcile_bp_committed_writes_with_120s_ttl(account_id: uuid.UUID) -> None:
    """``reconcile_bp_committed`` SETs broker BP with 120s TTL."""

    from app.services.risk_inflight_counters import reconcile_bp_committed

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    await reconcile_bp_committed(redis, account_id, broker_reported=Decimal("12345.67"))
    redis.set.assert_awaited_once_with(f"risk:bp_committed:{account_id}", "12345.67", ex=120)
