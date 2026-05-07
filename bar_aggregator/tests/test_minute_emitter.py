from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock

import asyncpg  # type: ignore[import-untyped]
import pytest

from bar_aggregator.app.aggregator import AggregatorEngine, VolumeSource
from bar_aggregator.app.bar_pubsub import BarPubSub, FINAL_REVISION
from bar_aggregator.app.minute_emitter import AGGREGATOR_PRIORITY, MinuteEmitter

pytestmark = [pytest.mark.unit]


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Connection:
    def __init__(self) -> None:
        self.execute = AsyncMock()

    def transaction(self) -> _Transaction:
        return _Transaction()


class _AcquireContext:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Connection:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self._conn)


class _PubSub:
    def __init__(self) -> None:
        self.publish_final = AsyncMock()


def _emitter(
    engine: AggregatorEngine,
    conn: _Connection,
    pubsub: _PubSub | None = None,
) -> tuple[MinuteEmitter, _PubSub]:
    actual_pubsub = pubsub or _PubSub()
    emitter = MinuteEmitter(
        engine=engine,
        bar_pubsub=cast(BarPubSub, actual_pubsub),
        pg_pool=cast(asyncpg.Pool, _Pool(conn)),
        canonical_id_lookup={42: "schwab:AAPL"},
    )
    return emitter, actual_pubsub


def _seed_bucket(
    engine: AggregatorEngine,
    bucket_start: dt.datetime,
    *,
    source: str = "schwab",
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100.5",
    volume: str | None = "1",
    trade_count: int = 1,
    volume_source: str = "tape",
) -> None:
    engine.apply_test_bucket(
        instrument_id=42,
        source=source,
        bucket_start=bucket_start,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=None if volume is None else Decimal(volume),
        trade_count=trade_count,
        volume_source=cast(VolumeSource, volume_source),
    )


def _execute_args(conn: _Connection) -> tuple[Any, ...]:
    return conn.execute.await_args.args


async def test_emits_one_1m_bar_from_60_1s_buckets() -> None:
    engine = AggregatorEngine()
    conn = _Connection()
    t0 = dt.datetime(2026, 5, 7, 15, 31, tzinfo=dt.UTC)
    start = t0 - dt.timedelta(seconds=60)

    for offset in range(60):
        _seed_bucket(
            engine,
            start + dt.timedelta(seconds=offset),
            open_=str(100 + offset),
            high=str(101 + offset),
            low=str(99 - offset),
            close=str(100.5 + offset),
            volume="2",
            trade_count=offset + 1,
        )

    emitter, _pubsub = _emitter(engine, conn)

    emitted = await emitter.tick(minute_close=t0)

    assert emitted == 1
    conn.execute.assert_awaited_once()
    args = _execute_args(conn)
    assert args[1] == 42
    assert args[2] == start
    assert args[5] == Decimal("100")
    assert args[6] == Decimal("160")
    assert args[7] == Decimal("40")
    assert args[8] == Decimal("159.5")
    assert args[9] == Decimal("120")
    assert args[10] == sum(range(1, 61))


async def test_skips_pair_with_no_buckets_in_window() -> None:
    engine = AggregatorEngine()
    conn = _Connection()
    t0 = dt.datetime(2026, 5, 7, 15, 31, tzinfo=dt.UTC)

    _seed_bucket(engine, t0 - dt.timedelta(seconds=61))
    emitter, _pubsub = _emitter(engine, conn)

    emitted = await emitter.tick(minute_close=t0)

    assert emitted == 0
    conn.execute.assert_not_awaited()


async def test_upsert_uses_priority_99_and_aggregator_source_prefix() -> None:
    engine = AggregatorEngine()
    conn = _Connection()
    t0 = dt.datetime(2026, 5, 7, 15, 31, tzinfo=dt.UTC)
    _seed_bucket(engine, t0 - dt.timedelta(seconds=1))
    emitter, _pubsub = _emitter(engine, conn)

    await emitter.tick(minute_close=t0)

    args = _execute_args(conn)
    assert args[3] == "aggregator-schwab"
    assert args[4] == AGGREGATOR_PRIORITY


async def test_publish_final_emits_1m_with_final_revision() -> None:
    engine = AggregatorEngine()
    conn = _Connection()
    t0 = dt.datetime(2026, 5, 7, 15, 31, tzinfo=dt.UTC)
    start = t0 - dt.timedelta(seconds=60)
    for offset in range(5):
        _seed_bucket(engine, start + dt.timedelta(seconds=offset))
    emitter, pubsub = _emitter(engine, conn)

    await emitter.tick(minute_close=t0)

    pubsub.publish_final.assert_awaited_once()
    snap = pubsub.publish_final.await_args.args[0]
    assert snap.tf == "1m"
    assert snap.revision == FINAL_REVISION
    assert snap.partial is False


async def test_volume_source_priority_tape_wins() -> None:
    engine = AggregatorEngine()
    conn = _Connection()
    t0 = dt.datetime(2026, 5, 7, 15, 31, tzinfo=dt.UTC)
    start = t0 - dt.timedelta(seconds=60)
    _seed_bucket(engine, start, volume_source="quote_proxy", volume=None)
    _seed_bucket(engine, start + dt.timedelta(seconds=1), volume_source="tape")
    emitter, pubsub = _emitter(engine, conn)

    await emitter.tick(minute_close=t0)

    snap = pubsub.publish_final.await_args.args[0]
    assert snap.volume_source == "tape"
