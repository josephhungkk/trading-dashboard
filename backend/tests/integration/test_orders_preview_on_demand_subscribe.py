"""Phase 9.7 — on-demand quote subscribe for preview.

Tests the full path from _get_market_mid (Redis miss) through
QuoteEngine.subscribe_one_shot to _native_notional returning a value, as well
as the timeout / 503 fallback.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from app._generated.broker.v1 import broker_pb2 as pb
from app.brokers.base import Contract
from app.services.orders_service import (
    PreviewUnavailable,
    _get_market_mid,
    _mid_from_tick,
    _one_shot_market_mid,
)
from app.services.quotes.engine import QuoteEngine
from app.services.quotes.registry import SubscriptionRegistry
from app.services.quotes.router import SourceHealthMap, SourceRouter

# ── helpers ────────────────────────────────────────────────────────────────


def _tick(
    canonical: str = "stock:AAPL:US",
    bid: str = "150.00",
    ask: str = "150.10",
    last: str = "150.05",
    source: str = "schwab",
) -> pb.QuoteMessage:
    received = Timestamp()
    received.GetCurrentTime()
    return pb.QuoteMessage(
        canonical_id=canonical,
        source=source,
        bid=bid,
        ask=ask,
        last=last,
        received_at=received,
    )


def _make_engine(redis: Any) -> QuoteEngine:
    """Engine with no real sidecar streams — inject ticks via _on_quote()."""
    registry = SubscriptionRegistry(cap_per_ws=100, cap_global=1000, sub_rate_limit_per_minute=1000)
    router = SourceRouter(
        config={"quote_source_priority": {"stock.US": ["schwab"]}},
        health=SourceHealthMap(),
    )
    return QuoteEngine(registry=registry, router=router, redis=redis)


def _contract(exchange: str = "SMART") -> Contract:
    return Contract(
        symbol="AAPL",
        exchange=exchange,
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol="AAPL",
    )


# ── _mid_from_tick unit tests ──────────────────────────────────────────────


def test_mid_from_tick_bid_ask_average() -> None:
    tick = _tick(bid="100.00", ask="100.20")
    mid = _mid_from_tick(tick)
    assert mid == Decimal("100.10")


def test_mid_from_tick_falls_back_to_last_when_no_bid_ask() -> None:
    tick = _tick(bid="", ask="", last="99.50")
    mid = _mid_from_tick(tick)
    assert mid == Decimal("99.50")


def test_mid_from_tick_returns_none_when_all_empty() -> None:
    tick = _tick(bid="", ask="", last="")
    assert _mid_from_tick(tick) is None


def test_mid_from_tick_ignores_zero_prices() -> None:
    tick = _tick(bid="0", ask="0", last="0")
    assert _mid_from_tick(tick) is None


# ── QuoteEngine.subscribe_one_shot ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_one_shot_returns_cached_immediately(redis: Any) -> None:
    """Fast path: in-process cache hit → no subscription needed."""
    engine = _make_engine(redis)
    tick = _tick()
    await engine._on_quote(tick)  # type: ignore[attr-defined]

    result = await engine.subscribe_one_shot("stock:AAPL:US", timeout_sec=0.1)

    assert result is not None
    assert result.canonical_id == "stock:AAPL:US"


@pytest.mark.asyncio
async def test_subscribe_one_shot_delivers_tick_on_subscribe(redis: Any) -> None:
    """Slow path: no cache → subscribe → inject tick → future resolves."""
    engine = _make_engine(redis)
    tick_to_inject = _tick()

    async def _inject_after_subscribe() -> None:
        await asyncio.sleep(0.05)
        await engine._on_quote(tick_to_inject)  # type: ignore[attr-defined]

    task = asyncio.create_task(_inject_after_subscribe())
    result = await engine.subscribe_one_shot("stock:AAPL:US", timeout_sec=2.0)
    await task

    assert result is not None
    assert result.canonical_id == "stock:AAPL:US"


@pytest.mark.asyncio
async def test_subscribe_one_shot_timeout_returns_none(redis: Any) -> None:
    """Timeout path: no tick arrives → returns None."""
    engine = _make_engine(redis)

    result = await engine.subscribe_one_shot("stock:AAPL:US", timeout_sec=0.05)

    assert result is None


@pytest.mark.asyncio
async def test_subscribe_one_shot_cleans_up_conflator_after_success(redis: Any) -> None:
    """After one-shot resolves, no extra conflator entries remain."""
    engine = _make_engine(redis)
    tick_to_inject = _tick()
    before = set(engine._conflators.keys())  # type: ignore[attr-defined]

    async def _inject() -> None:
        await asyncio.sleep(0.05)
        await engine._on_quote(tick_to_inject)  # type: ignore[attr-defined]

    task = asyncio.create_task(_inject())
    await engine.subscribe_one_shot("stock:AAPL:US", timeout_sec=2.0)
    await task

    after = set(engine._conflators.keys())  # type: ignore[attr-defined]
    assert after == before  # one-shot WS UUID was removed


@pytest.mark.asyncio
async def test_subscribe_one_shot_cleans_up_conflator_after_timeout(redis: Any) -> None:
    """After timeout, the fake WS UUID is still removed from conflator tables."""
    engine = _make_engine(redis)
    before = set(engine._conflators.keys())  # type: ignore[attr-defined]
    await engine.subscribe_one_shot("stock:AAPL:US", timeout_sec=0.05)

    after = set(engine._conflators.keys())  # type: ignore[attr-defined]
    assert after == before


# ── _get_market_mid with quote_engine ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_market_mid_redis_hit_skips_engine() -> None:
    """Redis cache hit → engine never called."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set("mkt:mid:265598", "123.45", ex=60)
    engine_mock = AsyncMock()

    mid = await _get_market_mid(
        redis,
        "265598",
        contract=_contract(),
        quote_engine=engine_mock,
    )

    assert mid == Decimal("123.45")
    engine_mock.subscribe_one_shot.assert_not_called()


@pytest.mark.asyncio
async def test_get_market_mid_falls_back_to_engine_on_miss(redis: Any) -> None:
    """Redis miss → engine.subscribe_one_shot called → mid returned and cached."""
    engine = _make_engine(redis)
    tick_to_inject = _tick(bid="200.00", ask="200.20")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _inject() -> None:
        await asyncio.sleep(0.05)
        await engine._on_quote(tick_to_inject)  # type: ignore[attr-defined]

    task = asyncio.create_task(_inject())
    mid = await _get_market_mid(
        r,
        "265598",
        contract=_contract(),
        quote_engine=engine,
    )
    await task

    assert mid == Decimal("200.10")
    cached = await r.get("mkt:mid:265598")
    assert cached is not None
    assert Decimal(cached) == Decimal("200.10")


@pytest.mark.asyncio
async def test_get_market_mid_raises_503_when_no_engine() -> None:
    """No engine supplied → raises PreviewUnavailable 503."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    with pytest.raises(PreviewUnavailable) as exc_info:
        await _get_market_mid(redis, "265598")

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"] == "market_mid_unavailable"


@pytest.mark.asyncio
async def test_get_market_mid_raises_503_on_engine_timeout(redis: Any) -> None:
    """Engine times out (no tick) → raises PreviewUnavailable 503."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    engine = _make_engine(redis)

    with pytest.raises(PreviewUnavailable) as exc_info:
        # subscribe_one_shot will time out because no tick is injected
        await _get_market_mid(
            r,
            "265598",
            contract=_contract(),
            quote_engine=engine,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"] == "market_mid_unavailable"


# ── _one_shot_market_mid edge cases ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_shot_market_mid_unknown_exchange_returns_none(redis: Any) -> None:
    """Unknown exchange code → can't derive country → returns None."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    engine = _make_engine(redis)
    contract = _contract(exchange="UNKNOWN_EXCHANGE_XYZ")

    result = await _one_shot_market_mid(r, "265598", contract, engine)

    assert result is None


@pytest.mark.asyncio
async def test_one_shot_market_mid_non_engine_object_returns_none(redis: Any) -> None:
    """Non-QuoteEngine object passed → returns None safely."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)

    result = await _one_shot_market_mid(r, "265598", _contract(), object())

    assert result is None
