"""Phase 10a.5 A4.3 (CRIT-1 fix): BrokerDiscoverer orphan-token UNLINK sweep.

The discoverer reaps stranded ``risk:pdt:tok:{account_id}:*`` and
``risk:bp:tok:{account_id}:*`` keys each tick per account, bounding the
token-leak window to one discoverer cycle even when a dispatch crashed between
the counter mutation and the broker ACK.

CRIT-1 fix: sweep is now scoped per account_id so in-flight tokens from OTHER
accounts are never deleted. The old blanket scan would silently nuke live tokens
from concurrent dispatches on different accounts, making revert_pdt/revert_bp
no-ops and leaving the counter permanently decremented.

The sweep is no-op when redis is None (test paths that construct BrokerDiscoverer
without injecting redis must keep working).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.brokers import BrokerDiscoverer

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]

_AID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_AID2 = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _fake_discoverer(redis: object | None) -> BrokerDiscoverer:
    """Build a BrokerDiscoverer with stubbed registry + session_factory."""
    registry = MagicMock()
    session_factory = MagicMock(spec=async_sessionmaker)
    return BrokerDiscoverer(registry, session_factory, interval_seconds=30.0, redis=redis)


async def test_unlink_sweep_no_op_when_redis_none() -> None:
    """Discoverer constructed without redis still functions; sweep returns 0."""
    discoverer = _fake_discoverer(redis=None)
    assert await discoverer._unlink_risk_counter_orphans(_AID) == 0


async def test_unlink_sweep_clears_pdt_and_bp_tokens_for_account() -> None:
    """Sweep clears only token keys matching the given account_id prefix."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    tok_pdt_1 = f"risk:pdt:tok:{_AID}:abc"
    tok_pdt_2 = f"risk:pdt:tok:{_AID}:def"
    tok_bp_1 = f"risk:bp:tok:{_AID}:xyz"
    # Token belonging to a DIFFERENT account — must NOT be swept.
    tok_other = f"risk:pdt:tok:{_AID2}:other"
    await redis.set(tok_pdt_1, "1")
    await redis.set(tok_pdt_2, "1")
    await redis.set(tok_bp_1, "100.50")
    await redis.set(tok_other, "1")
    # Counter keys MUST be preserved (only token keys are reaped).
    await redis.set(f"risk:pdt:{_AID}", "5")
    await redis.set(f"risk:bp_committed:{_AID}", "1234")

    discoverer = _fake_discoverer(redis=redis)
    swept = await discoverer._unlink_risk_counter_orphans(_AID)

    assert swept == 3
    # Token keys for _AID gone
    assert await redis.get(tok_pdt_1) is None
    assert await redis.get(tok_pdt_2) is None
    assert await redis.get(tok_bp_1) is None
    # Token for OTHER account untouched
    assert await redis.get(tok_other) == "1"
    # Counter keys retained
    assert await redis.get(f"risk:pdt:{_AID}") == "5"
    assert await redis.get(f"risk:bp_committed:{_AID}") == "1234"


async def test_unlink_sweep_does_not_touch_other_account_tokens() -> None:
    """CRIT-1: tokens from other accounts are never deleted by this account's sweep."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    tok_aid1 = f"risk:pdt:tok:{_AID}:token1"
    tok_aid2 = f"risk:pdt:tok:{_AID2}:token2"
    await redis.set(tok_aid1, "1")
    await redis.set(tok_aid2, "1")

    discoverer = _fake_discoverer(redis=redis)
    # Sweep only for _AID
    swept = await discoverer._unlink_risk_counter_orphans(_AID)

    assert swept == 1
    assert await redis.get(tok_aid1) is None
    assert await redis.get(tok_aid2) == "1"  # untouched


async def test_unlink_sweep_empty_state_returns_zero() -> None:
    """No token keys present -> sweep is a no-op."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    discoverer = _fake_discoverer(redis=redis)
    assert await discoverer._unlink_risk_counter_orphans(_AID) == 0


async def test_unlink_sweep_swallows_redis_failure() -> None:
    """A redis scan failure is logged + counted, NOT raised — discoverer continues."""
    redis = AsyncMock()
    redis.scan = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.unlink = AsyncMock()

    discoverer = _fake_discoverer(redis=redis)
    # Must not raise.
    result = await discoverer._unlink_risk_counter_orphans(_AID)
    assert result == 0
