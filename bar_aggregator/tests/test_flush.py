from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock

import asyncpg  # type: ignore[import-untyped]
import pytest

from bar_aggregator.app.aggregator import AggregatorEngine, BucketFields
from bar_aggregator.app.bar_pubsub import BarPubSub, BarSnapshot, FINAL_REVISION
from bar_aggregator.app.flush import Flusher
from bar_aggregator.app.wal import WAL

pytestmark = [pytest.mark.unit]


class _AcquireContext:
    def __init__(self, connection: "_Connection") -> None:
        self._connection = connection

    async def __aenter__(self) -> "_Connection":
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        return False


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        return False


class _Connection:
    def __init__(self) -> None:
        self.executemany = AsyncMock(return_value=None)

    def transaction(self) -> _Transaction:
        return _Transaction()


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self._connection)


class _StubWAL:
    def __init__(self) -> None:
        self.ack_flushed = AsyncMock(return_value=None)
        self.set_last_flushed = AsyncMock(return_value=None)


class _StubBarPubSub:
    def __init__(self) -> None:
        self.publish_final = AsyncMock(return_value=None)


def _flusher(
    engine: AggregatorEngine,
    *,
    connection: _Connection | None = None,
    wal: _StubWAL | None = None,
    bar_pubsub: _StubBarPubSub | None = None,
    wal_entry_id_resolver: Callable[[int, dt.datetime], str | None] | None = None,
) -> tuple[Flusher, _Connection, _StubWAL, _StubBarPubSub]:
    stub_connection = connection or _Connection()
    stub_wal = wal or _StubWAL()
    stub_bar_pubsub = bar_pubsub or _StubBarPubSub()
    return (
        Flusher(
            engine=engine,
            wal=cast(WAL, stub_wal),
            bar_pubsub=cast(BarPubSub, stub_bar_pubsub),
            pg_pool=cast(asyncpg.Pool, _Pool(stub_connection)),
            canonical_id_lookup={1: "AAPL.US"},
            wal_entry_id_resolver=wal_entry_id_resolver,
        ),
        stub_connection,
        stub_wal,
        stub_bar_pubsub,
    )


async def test_only_closed_buckets_flushed() -> None:
    engine = AggregatorEngine()
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    engine.apply_test_bucket(1, "schwab", t0, **_bucket_fields(open="100"))
    engine.apply_test_bucket(1, "schwab", t0 + dt.timedelta(seconds=1), **_bucket_fields(open="200"))
    flusher, connection, _wal, _bar_pubsub = _flusher(engine)

    rows = await flusher.flush_once(now=t0 + dt.timedelta(seconds=1, milliseconds=500))

    assert rows == 1
    records = connection.executemany.await_args.args[1]
    assert len(records) == 1
    assert records[0][1] == t0
    assert t0 not in engine.buckets[(1, "schwab")]
    assert t0 + dt.timedelta(seconds=1) in engine.buckets[(1, "schwab")]


async def test_in_flight_bucket_never_flushed() -> None:
    engine = AggregatorEngine()
    now = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    bucket_start = now - dt.timedelta(milliseconds=400)
    engine.apply_test_bucket(1, "schwab", bucket_start, **_bucket_fields())
    flusher, connection, _wal, _bar_pubsub = _flusher(engine)

    rows = await flusher.flush_once(now=now)

    assert rows == 0
    connection.executemany.assert_not_awaited()
    assert bucket_start in engine.buckets[(1, "schwab")]


async def test_flush_success_calls_ack_flushed_and_publish_final() -> None:
    engine = AggregatorEngine()
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    engine.apply_test_bucket(1, "schwab", t0, **_bucket_fields(close="101"))
    flusher, _connection, wal, bar_pubsub = _flusher(
        engine,
        wal_entry_id_resolver=lambda _instrument_id, _bucket_start: "1700000000-0",
    )

    rows = await flusher.flush_once(now=t0 + dt.timedelta(seconds=1))

    assert rows == 1
    wal.ack_flushed.assert_awaited_once_with(1, "1700000000-0")
    wal.set_last_flushed.assert_awaited_once_with(1, t0)
    bar_pubsub.publish_final.assert_awaited_once()
    snap = bar_pubsub.publish_final.await_args.args[0]
    assert isinstance(snap, BarSnapshot)
    assert snap.revision == FINAL_REVISION
    assert snap.partial is False
    assert snap.canonical_id == "AAPL.US"
    assert snap.close == Decimal("101")


async def test_pg_operational_error_pauses_flush() -> None:
    engine = AggregatorEngine()
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    engine.apply_test_bucket(1, "schwab", t0, **_bucket_fields())
    connection = _Connection()
    connection.executemany.side_effect = asyncpg.exceptions.OperationalError("pg down")
    flusher, _connection, wal, _bar_pubsub = _flusher(
        engine,
        connection=connection,
        wal_entry_id_resolver=lambda _instrument_id, _bucket_start: "1700000000-0",
    )

    rows = await flusher.flush_once(now=t0 + dt.timedelta(seconds=1))

    assert rows == 0
    assert flusher._paused_since == t0 + dt.timedelta(seconds=1)
    assert t0 in engine.buckets[(1, "schwab")]
    wal.ack_flushed.assert_not_awaited()


async def test_flush_resumes_after_pg_recovers() -> None:
    engine = AggregatorEngine()
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    engine.apply_test_bucket(1, "schwab", t0, **_bucket_fields())
    connection = _Connection()
    connection.executemany.side_effect = [
        asyncpg.exceptions.OperationalError("pg down"),
        None,
    ]
    flusher, _connection, _wal, _bar_pubsub = _flusher(engine, connection=connection)

    first_rows = await flusher.flush_once(now=t0 + dt.timedelta(seconds=1))
    second_rows = await flusher.flush_once(now=t0 + dt.timedelta(seconds=2))

    assert first_rows == 0
    assert second_rows == 1
    assert flusher._paused_since is None
    assert (1, "schwab") not in engine.buckets


def _bucket_fields(
    *,
    open: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100.5",
    volume: str = "42",
) -> BucketFields:
    return BucketFields(
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        volume_source="tape",
        trade_count=3,
    )
