"""Phase 10a.5 A4.3: BrokerDiscoverer orphan-token UNLINK sweep.

The discoverer reaps stranded ``risk:pdt:tok:*`` and ``risk:bp:tok:*`` keys
each tick before any reconcile, bounding the token-leak window to one
discoverer cycle even when a dispatch crashed between the counter mutation
and the broker ACK. The sweep is no-op when redis is None (test paths
that construct BrokerDiscoverer without injecting redis must keep working).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.brokers import BrokerDiscoverer

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]


def _fake_discoverer(redis: object | None) -> BrokerDiscoverer:
    """Build a BrokerDiscoverer with stubbed registry + session_factory."""
    registry = MagicMock()
    session_factory = MagicMock(spec=async_sessionmaker)
    return BrokerDiscoverer(registry, session_factory, interval_seconds=30.0, redis=redis)


async def test_unlink_sweep_no_op_when_redis_none() -> None:
    """Discoverer constructed without redis still functions; sweep returns 0."""
    discoverer = _fake_discoverer(redis=None)
    assert await discoverer._unlink_risk_counter_orphans() == 0


async def test_unlink_sweep_clears_pdt_and_bp_tokens() -> None:
    """Both pdt and bp token namespaces are swept."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set("risk:pdt:tok:abc", "1")
    await redis.set("risk:pdt:tok:def", "1")
    await redis.set("risk:bp:tok:xyz", "100.50")
    # Counter keys MUST be preserved (only token keys are reaped).
    await redis.set("risk:pdt:00000000-0000-0000-0000-000000000001", "5")
    await redis.set("risk:bp_committed:00000000-0000-0000-0000-000000000001", "1234")

    discoverer = _fake_discoverer(redis=redis)
    swept = await discoverer._unlink_risk_counter_orphans()

    assert swept == 3
    # Token keys gone
    assert await redis.get("risk:pdt:tok:abc") is None
    assert await redis.get("risk:pdt:tok:def") is None
    assert await redis.get("risk:bp:tok:xyz") is None
    # Counter keys retained
    assert await redis.get("risk:pdt:00000000-0000-0000-0000-000000000001") == "5"
    assert await redis.get("risk:bp_committed:00000000-0000-0000-0000-000000000001") == "1234"


async def test_unlink_sweep_empty_state_returns_zero() -> None:
    """No token keys present -> sweep is a no-op."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    discoverer = _fake_discoverer(redis=redis)
    assert await discoverer._unlink_risk_counter_orphans() == 0


async def test_unlink_sweep_swallows_redis_failure() -> None:
    """A redis scan failure is logged + counted, NOT raised — discoverer continues."""
    redis = AsyncMock()
    redis.scan = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.unlink = AsyncMock()

    discoverer = _fake_discoverer(redis=redis)
    # Must not raise.
    result = await discoverer._unlink_risk_counter_orphans()
    assert result == 0
