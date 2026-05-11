"""Phase 10a + 10a.5 A4 — token-bearing in-flight counter API tests.

A4 widened the counter API: ``decrement_pdt`` and ``commit_bp`` now return
``(value, token)`` and ``revert_*`` / ``commit_*_finalize`` consume the token
via atomic Lua so double-revert is a no-op (HIGH-2). The reader helpers
(``inflight_*``, ``reconcile_*``) are unchanged.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import fakeredis.aioredis
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]


@pytest.fixture
def account_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
async def redis_fake():  # type: ignore[no-untyped-def]
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ─── PDT counter ────────────────────────────────────────────────────────


async def test_decrement_pdt_returns_value_and_token(redis_fake, account_id: uuid.UUID) -> None:
    """Cold cache + broker_reported -> seed, DECR, return (value, token)."""
    from app.services.risk_inflight_counters import decrement_pdt

    new_value, token = await decrement_pdt(redis_fake, account_id, broker_reported=3)
    assert new_value == 2
    assert token  # 32-hex-char uuid4().hex
    assert len(token) == 32


async def test_decrement_pdt_commit_keeps_counter(redis_fake, account_id: uuid.UUID) -> None:
    """commit_pdt finalizes the decrement — counter stays at decremented value."""
    from app.services.risk_inflight_counters import (
        commit_pdt,
        decrement_pdt,
        inflight_pdt_remaining,
    )

    _, token = await decrement_pdt(redis_fake, account_id, broker_reported=3)
    await commit_pdt(redis_fake, account_id, token)
    assert await inflight_pdt_remaining(redis_fake, account_id) == 2


async def test_decrement_pdt_revert_restores(redis_fake, account_id: uuid.UUID) -> None:
    """revert_pdt rolls the decrement back to broker_reported."""
    from app.services.risk_inflight_counters import (
        decrement_pdt,
        inflight_pdt_remaining,
        revert_pdt,
    )

    _, token = await decrement_pdt(redis_fake, account_id, broker_reported=3)
    final = await revert_pdt(redis_fake, account_id, token)
    assert final == 3
    assert await inflight_pdt_remaining(redis_fake, account_id) == 3


async def test_pdt_double_revert_is_noop(redis_fake, account_id: uuid.UUID) -> None:
    """HIGH-2: a second revert with the same token does NOT double-credit."""
    from app.services.risk_inflight_counters import decrement_pdt, revert_pdt

    _, token = await decrement_pdt(redis_fake, account_id, broker_reported=3)
    first = await revert_pdt(redis_fake, account_id, token)
    second = await revert_pdt(redis_fake, account_id, token)
    assert first == 3
    assert second == 3  # idempotent: counter NOT incremented twice


async def test_inflight_pdt_remaining_returns_int_when_set(
    redis_fake, account_id: uuid.UUID
) -> None:
    """Counter present in Redis -> return int(value)."""
    from app.services.risk_inflight_counters import (
        _pdt_key,
        inflight_pdt_remaining,
    )

    await redis_fake.set(_pdt_key(account_id), "2")
    got = await inflight_pdt_remaining(redis_fake, account_id)
    assert got == 2


async def test_inflight_pdt_remaining_returns_none_when_unset(
    redis_fake, account_id: uuid.UUID
) -> None:
    """Key absent -> None so caller can fall back to broker-reported value."""
    from app.services.risk_inflight_counters import inflight_pdt_remaining

    got = await inflight_pdt_remaining(redis_fake, account_id)
    assert got is None


async def test_reconcile_pdt_writes_with_120s_ttl(redis_fake, account_id: uuid.UUID) -> None:
    """reconcile_pdt SETs the broker-reported value with 120s TTL."""
    from app.services.risk_inflight_counters import _pdt_key, reconcile_pdt

    await reconcile_pdt(redis_fake, account_id, broker_reported=4)
    assert await redis_fake.get(_pdt_key(account_id)) == "4"
    ttl = await redis_fake.ttl(_pdt_key(account_id))
    assert 100 < ttl <= 120


async def test_decrement_pdt_setnx_does_not_overwrite_reconciled(
    redis_fake, account_id: uuid.UUID
) -> None:
    """Existing reconciled value is preserved across decrement_pdt seed."""
    from app.services.risk_inflight_counters import (
        _pdt_key,
        decrement_pdt,
    )

    # simulate a prior reconcile to broker-reported 5
    await redis_fake.set(_pdt_key(account_id), "5", ex=120)
    new_value, _ = await decrement_pdt(redis_fake, account_id, broker_reported=3)
    # SET NX skipped because the key existed -> DECR runs against 5
    assert new_value == 4


# ─── BP counter ─────────────────────────────────────────────────────────


async def test_commit_bp_returns_total_and_token(redis_fake, account_id: uuid.UUID) -> None:
    """commit_bp INCRBYFLOATs notional and returns (Decimal_total, token)."""
    from app.services.risk_inflight_counters import commit_bp

    total, token = await commit_bp(redis_fake, account_id, Decimal("15000"))
    assert total == Decimal("15000")
    assert isinstance(total, Decimal)
    assert len(token) == 32


async def test_bp_commit_revert_restores(redis_fake, account_id: uuid.UUID) -> None:
    """revert_bp subtracts the previously-committed notional."""
    from app.services.risk_inflight_counters import (
        commit_bp,
        inflight_bp_committed,
        revert_bp,
    )

    _, token = await commit_bp(redis_fake, account_id, Decimal("1234.56"))
    final = await revert_bp(redis_fake, account_id, token)
    assert final == Decimal("0")
    assert await inflight_bp_committed(redis_fake, account_id) == Decimal("0")


async def test_bp_double_revert_is_noop(redis_fake, account_id: uuid.UUID) -> None:
    """HIGH-2: a second BP revert with the same token does NOT double-subtract."""
    from app.services.risk_inflight_counters import commit_bp, revert_bp

    _, token = await commit_bp(redis_fake, account_id, Decimal("500"))
    first = await revert_bp(redis_fake, account_id, token)
    second = await revert_bp(redis_fake, account_id, token)
    assert first == Decimal("0")
    assert second == Decimal("0")  # idempotent


async def test_bp_commit_finalize_keeps_counter(redis_fake, account_id: uuid.UUID) -> None:
    """commit_bp_finalize marks the commit finalized; counter remains."""
    from app.services.risk_inflight_counters import (
        commit_bp,
        commit_bp_finalize,
        inflight_bp_committed,
    )

    _, token = await commit_bp(redis_fake, account_id, Decimal("500"))
    await commit_bp_finalize(redis_fake, account_id, token)
    assert await inflight_bp_committed(redis_fake, account_id) == Decimal("500")


async def test_inflight_bp_committed_returns_decimal_zero_when_unset(
    redis_fake, account_id: uuid.UUID
) -> None:
    """Key absent -> Decimal('0') (never None)."""
    from app.services.risk_inflight_counters import inflight_bp_committed

    got = await inflight_bp_committed(redis_fake, account_id)
    assert got == Decimal("0")
    assert isinstance(got, Decimal)


async def test_reconcile_bp_committed_writes_with_120s_ttl(
    redis_fake, account_id: uuid.UUID
) -> None:
    """reconcile_bp_committed SETs broker BP with 120s TTL."""
    from app.services.risk_inflight_counters import (
        _bp_key,
        reconcile_bp_committed,
    )

    await reconcile_bp_committed(redis_fake, account_id, broker_reported=Decimal("12345.67"))
    assert await redis_fake.get(_bp_key(account_id)) == "12345.67"
    ttl = await redis_fake.ttl(_bp_key(account_id))
    assert 100 < ttl <= 120
