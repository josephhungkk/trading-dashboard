from __future__ import annotations

import asyncio
import datetime as dt
import json
from decimal import Decimal
from typing import cast

import pytest
import redis.asyncio as redis_async  # type: ignore[import-untyped]
from prometheus_client import REGISTRY

from bar_aggregator.app.bar_pubsub import (
    FINAL_REVISION,
    BarPubSub,
    BarSnapshot,
)

pytestmark = [pytest.mark.unit]


class FakeRedis:
    def __init__(self, fail_channel: str | None = None) -> None:
        self.published: list[tuple[str, bytes]] = []
        self._fail_channel = fail_channel

    async def publish(self, channel: str, payload: bytes) -> int:
        if channel == self._fail_channel:
            raise RuntimeError("publish failed")
        self.published.append((channel, payload))
        return 1


def _snap(
    *,
    canonical_id: str = "schwab:AAPL",
    instrument_id: int = 1,
    tf: str = "1s",
    revision: int = 1,
    partial: bool = True,
) -> BarSnapshot:
    return BarSnapshot(
        canonical_id=canonical_id,
        instrument_id=instrument_id,
        tf=tf,
        bucket_start=dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC),
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.50"),
        close=Decimal("100.50"),
        volume=Decimal("42"),
        volume_source="tape",
        trade_count=3,
        revision=revision,
        partial=partial,
    )


def _counter_value(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    return 0.0 if value is None else value


async def test_20_ticks_within_window_yields_at_most_3_publishes() -> None:
    redis = FakeRedis()
    pubsub = BarPubSub(cast(redis_async.Redis, redis))

    for revision in range(20):
        pubsub.update(_snap(revision=revision))

    await asyncio.sleep(0.05)
    await pubsub.aclose()

    assert len(redis.published) <= 3


async def test_publish_final_bypasses_coalescer() -> None:
    redis = FakeRedis()
    pubsub = BarPubSub(cast(redis_async.Redis, redis), max_interval_ms=10_000)

    pubsub.update(_snap(revision=1))
    await pubsub.publish_final(_snap(revision=2))
    await pubsub.aclose()

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    decoded = json.loads(payload.decode())
    assert channel == "bar.schwab:AAPL.1s"
    assert decoded["revision"] == FINAL_REVISION
    assert decoded["partial"] is False


async def test_callback_error_in_one_channel_does_not_crash_others() -> None:
    redis = FakeRedis(fail_channel="bar.schwab:MSFT.1s")
    pubsub = BarPubSub(cast(redis_async.Redis, redis))
    errors_before = _counter_value("bar_aggregator_pubsub_callback_errors_total")

    pubsub.update(_snap(canonical_id="schwab:MSFT", instrument_id=2))
    pubsub.update(_snap(canonical_id="schwab:AAPL", instrument_id=1))

    await asyncio.sleep(0.05)
    await pubsub.aclose()

    assert [channel for channel, _payload in redis.published] == ["bar.schwab:AAPL.1s"]
    assert _counter_value("bar_aggregator_pubsub_callback_errors_total") == errors_before + 1


async def test_max_channels_cap_drops_overflow() -> None:
    redis = FakeRedis()
    pubsub = BarPubSub(cast(redis_async.Redis, redis), max_channels=2)
    cap_hits_before = _counter_value("bar_aggregator_pubsub_channel_cap_hit_total")

    pubsub.update(_snap(canonical_id="schwab:AAPL", instrument_id=1))
    pubsub.update(_snap(canonical_id="schwab:MSFT", instrument_id=2))
    pubsub.update(_snap(canonical_id="schwab:NVDA", instrument_id=3))

    await asyncio.sleep(0.05)
    await pubsub.aclose()

    assert len(pubsub._channels) == 2
    assert _counter_value("bar_aggregator_pubsub_channel_cap_hit_total") == cap_hits_before + 1
    assert {channel for channel, _payload in redis.published} == {
        "bar.schwab:AAPL.1s",
        "bar.schwab:MSFT.1s",
    }


async def test_aclose_cancels_pending_tasks() -> None:
    redis = FakeRedis()
    pubsub = BarPubSub(cast(redis_async.Redis, redis), max_interval_ms=10_000)

    pubsub.update(_snap())
    coalescer = pubsub._channels["bar.schwab:AAPL.1s"]
    task = coalescer._task
    assert task is not None

    await pubsub.aclose()

    assert task.done()
