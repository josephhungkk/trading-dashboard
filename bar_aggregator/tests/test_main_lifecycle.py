from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock

import asyncpg  # type: ignore[import-untyped]
import fakeredis.aioredis as fakeredis_async
import pytest
import redis.asyncio as redis_async  # type: ignore[import-untyped]
from aiohttp import ClientSession

from bar_aggregator.app.config import Settings
from bar_aggregator.app.main import AggregatorApp
from bar_aggregator.app.wal import WAL, WAL_TRUNCATED_TOTAL, WALTickRecord

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


class _Connection:
    def __init__(self) -> None:
        self.fetch = AsyncMock(return_value=[{"id": 42, "canonical_id": "AAPL.US"}])
        self.copy_records_to_table = AsyncMock(return_value=None)


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.close = AsyncMock(return_value=None)

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self.connection)


class _RecordingPubSub:
    def __init__(self) -> None:
        self.psubscribe = AsyncMock(return_value=None)
        self.unsubscribe = AsyncMock(return_value=None)
        self.aclose = AsyncMock(return_value=None)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            await asyncio.sleep(60)
            yield {"type": "sleep"}


class _RecordingRedis:
    def __init__(self) -> None:
        self.pubsub_instance = _RecordingPubSub()
        self.aclose = AsyncMock(return_value=None)

    def pubsub(self) -> _RecordingPubSub:
        return self.pubsub_instance

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        del match
        if False:
            yield ""


async def test_app_start_then_stop_clean() -> None:
    redis = _RecordingRedis()
    pool = _Pool()
    app = AggregatorApp(
        _settings(http_port=0),
        _test_overrides={
            "redis": cast(redis_async.Redis, redis),
            "pg_pool": cast(asyncpg.Pool, pool),
            "canonical_id_lookup": {42: "AAPL.US"},
            "http_host": "127.0.0.1",
        },
    )

    await app.start()
    await asyncio.wait_for(app._consumer_ready.wait(), timeout=1)
    redis.pubsub_instance.psubscribe.assert_awaited_once_with("quote.*")
    assert app._bar_pubsub is not None
    aclose = AsyncMock(return_value=None)
    app._bar_pubsub.aclose = aclose  # type: ignore[method-assign]

    await app.stop()

    aclose.assert_awaited_once()


async def test_wal_replay_resumes_engine_state() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    pool = _Pool()
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    await wal.append(_tick_record(instrument_id=42, ts=t0))
    await wal.append(_quote_record(instrument_id=43, ts=t0 + dt.timedelta(seconds=1)))
    app = _app(redis, pool, canonical_ids={42: "AAPL.US", 43: "MSFT.US"})

    await app.start()
    try:
        assert app._engine is not None
        assert app._engine.peek_bucket(42, t0) is not None
        assert app._engine.peek_bucket(43, t0 + dt.timedelta(seconds=1)) is not None
    finally:
        await app.stop()


async def test_gap_detected_error_pauses_instrument() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    pool = _Pool()
    wal = WAL(redis=redis, shard=0, flush_interval_ms=1000)
    t0 = dt.datetime(2026, 5, 7, 15, 30, tzinfo=dt.UTC)
    await wal.set_last_flushed(instrument_id=42, ts=t0)
    await wal.append(_tick_record(instrument_id=42, ts=t0 + dt.timedelta(seconds=3)))
    before = WAL_TRUNCATED_TOTAL.labels(instrument="42")._value.get()
    app = _app(redis, pool)

    await app.start()
    try:
        assert 42 in app._paused_instruments
        after = WAL_TRUNCATED_TOTAL.labels(instrument="42")._value.get()
        assert after == before + 1
    finally:
        await app.stop()


async def test_healthz_returns_200_when_healthy() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    pool = _Pool()
    app = _app(redis, pool)

    await app.start()
    url = _http_base_url(app)
    try:
        async with ClientSession() as session:
            async with session.get(f"{url}/healthz") as response:
                body = await response.json()
                assert response.status == 200
                assert body["status"] == "ok"
    finally:
        await app.stop()


async def test_shard_filter_drops_other_shards_messages() -> None:
    redis = fakeredis_async.FakeRedis(decode_responses=True)
    pool = _Pool()
    app = _app(
        redis,
        pool,
        settings=_settings(http_port=0, aggregator_shard=0, aggregator_shard_count=2),
        canonical_ids={42: "AAPL.US", 43: "MSFT.US"},
    )

    await app.start()
    await asyncio.wait_for(app._consumer_ready.wait(), timeout=1)
    try:
        t0 = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
        await redis.publish("quote.test", _message(instrument_id=43, ts=t0))
        await asyncio.sleep(0.05)
        assert app._engine is not None
        assert app._engine.peek_bucket(43, t0) is None

        await redis.publish("quote.test", _message(instrument_id=42, ts=t0))
        await _wait_for_bucket(app, instrument_id=42, bucket_start=t0)
        assert app._engine.peek_bucket(42, t0) is not None
    finally:
        await app.stop()


def _app(
    redis: fakeredis_async.FakeRedis,
    pool: _Pool,
    *,
    settings: Settings | None = None,
    canonical_ids: dict[int, str] | None = None,
) -> AggregatorApp:
    return AggregatorApp(
        settings or _settings(http_port=0),
        _test_overrides={
            "redis": cast(redis_async.Redis, redis),
            "pg_pool": cast(asyncpg.Pool, pool),
            "canonical_id_lookup": canonical_ids or {42: "AAPL.US"},
            "http_host": "127.0.0.1",
        },
    )


def _settings(
    *,
    http_port: int,
    aggregator_shard: int = 0,
    aggregator_shard_count: int = 1,
) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        database_url="postgresql://example.invalid/dashboard",
        http_port=http_port,
        aggregator_shard=aggregator_shard,
        aggregator_shard_count=aggregator_shard_count,
        flush_interval_ms=60_000,
    )


def _http_base_url(app: AggregatorApp) -> str:
    assert app._http_site is not None
    server = app._http_site._server
    assert server is not None
    sockets = server.sockets
    assert sockets is not None
    port = sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}"


async def _wait_for_bucket(
    app: AggregatorApp,
    *,
    instrument_id: int,
    bucket_start: dt.datetime,
) -> None:
    for _ in range(20):
        assert app._engine is not None
        if app._engine.peek_bucket(instrument_id, bucket_start) is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("bucket was not created")


def _message(*, instrument_id: int, ts: dt.datetime) -> str:
    return json.dumps(
        {
            "kind": "tick",
            "instrument_id": instrument_id,
            "source": "schwab",
            "ts": ts.isoformat(),
            "price": "100.25",
            "volume": "3",
        }
    )


def _tick_record(*, instrument_id: int, ts: dt.datetime) -> WALTickRecord:
    return WALTickRecord(
        entry_id="",
        instrument_id=instrument_id,
        source="schwab",
        ts=ts,
        price=Decimal("100.25"),
        volume=Decimal("3"),
        bid=None,
        ask=None,
        kind="tick",
    )


def _quote_record(*, instrument_id: int, ts: dt.datetime) -> WALTickRecord:
    return WALTickRecord(
        entry_id="",
        instrument_id=instrument_id,
        source="ibkr",
        ts=ts,
        price=None,
        volume=None,
        bid=Decimal("100.10"),
        ask=Decimal("100.20"),
        kind="quote",
    )
