"""Async entrypoint and lifecycle for the bar aggregator service."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import signal
import time
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any, TypeVar, TypedDict, cast

import asyncpg  # type: ignore[import-untyped]
import redis.asyncio as redis_async  # type: ignore[import-untyped]
import structlog
import uvloop
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from bar_aggregator.app.aggregator import AggregatorEngine, BucketState
from bar_aggregator.app.bar_pubsub import BarPubSub, BarSnapshot
from bar_aggregator.app.config import Settings
from bar_aggregator.app.flush import Flusher
from bar_aggregator.app.metrics import (
    CONSUMER_ERRORS_TOTAL,
    FLUSH_LAG_SECONDS,
    IDLE_SECONDS,
    PG_UNREACHABLE_SECONDS,
    TICKS_CONSUMED_TOTAL,
    WAL_REPLAYED_TOTAL,
)
from bar_aggregator.app.minute_emitter import MinuteEmitter
from bar_aggregator.app.wal import GapDetectedError, WAL, WALTickRecord

log = structlog.get_logger(__name__)
_T = TypeVar("_T")

_INSTRUMENTS_SQL = """
SELECT id, symbol || '.' || COALESCE(exchange, 'XX') AS canonical_id
FROM instruments
"""


class _TestOverrides(TypedDict, total=False):
    redis: redis_async.Redis
    pg_pool: asyncpg.Pool
    canonical_id_lookup: dict[int, str]
    http_host: str


class AggregatorApp:
    def __init__(self, settings: Settings, _test_overrides: _TestOverrides | None = None) -> None:
        self._settings = settings
        self._test_overrides = _test_overrides or {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._paused_instruments: set[int] = set()
        self._wal_entry_id_by_bucket: dict[tuple[int, dt.datetime], str] = {}
        self._canonical_id_lookup: dict[int, str] = self._test_overrides.get(
            "canonical_id_lookup",
            {},
        )
        self._canonical_id_refreshed_at = 0.0
        self._consumer_ready = asyncio.Event()

        self._redis: redis_async.Redis | None = self._test_overrides.get("redis")
        self._pg_pool: asyncpg.Pool | None = self._test_overrides.get("pg_pool")
        self._wal: WAL | None = None
        self._bar_pubsub: BarPubSub | None = None
        self._engine: AggregatorEngine | None = None
        self._flusher: Flusher | None = None
        self._minute_emitter: MinuteEmitter | None = None
        self._pubsub: Any | None = None
        self._http_runner: web.AppRunner | None = None
        self._http_site: web.TCPSite | None = None

    async def start(self) -> None:
        try:
            await self._build_components()
            await self._replay_wal()
            await self._start_http()
            self._tasks = [
                asyncio.create_task(self._consume_quotes(), name="bar-aggregator-consumer"),
                asyncio.create_task(
                    self._refresh_canonical_ids_loop(),
                    name="canonical-id-refresh",
                ),
                asyncio.create_task(
                    self._require_flusher().flush_loop(stop=self._stop_event),
                    name="bar-aggregator-flusher",
                ),
                asyncio.create_task(
                    self._require_minute_emitter().run_loop(stop=self._stop_event),
                    name="bar-aggregator-minute-emitter",
                ),
            ]
            self._install_signal_handlers()
            log.info("bar_aggregator.started", http_port=self._settings.http_port)
        except (Exception,) as exc:
            log.critical("bar_aggregator.start_failed", exc_info=exc)
            with contextlib.suppress(Exception):
                await self.stop()
            raise

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._bar_pubsub is not None:
            await self._bar_pubsub.aclose()
            self._bar_pubsub = None

        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe()
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None

        if self._http_runner is not None:
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    async def _build_components(self) -> None:
        if self._redis is None:
            self._redis = redis_async.from_url(self._settings.redis_url, decode_responses=False)
        if self._pg_pool is None:
            self._pg_pool = await asyncpg.create_pool(
                self._settings.database_url,
                min_size=2,
                max_size=10,
            )

        self._wal = WAL(
            self._redis,
            shard=self._settings.aggregator_shard,
            flush_interval_ms=self._settings.flush_interval_ms,
        )
        self._bar_pubsub = BarPubSub(self._redis)
        self._engine = AggregatorEngine()
        if "canonical_id_lookup" not in self._test_overrides:
            await self._refresh_canonical_ids()

        self._flusher = Flusher(
            engine=self._engine,
            wal=self._wal,
            bar_pubsub=self._bar_pubsub,
            pg_pool=self._pg_pool,
            canonical_id_lookup=self._canonical_id_lookup,
            flush_interval_ms=self._settings.flush_interval_ms,
            wal_entry_id_resolver=self._pop_wal_entry_id,
        )
        self._minute_emitter = MinuteEmitter(
            engine=self._engine,
            bar_pubsub=self._bar_pubsub,
            pg_pool=self._pg_pool,
            canonical_id_lookup=self._canonical_id_lookup,
        )

    async def _replay_wal(self) -> None:
        keys = [key async for key in self._scan_wal_stream_keys()]
        for key in keys:
            instrument_id = self._instrument_id_from_wal_key(key)
            try:
                async for record in self._require_wal().replay(instrument_id=instrument_id):
                    self._apply_wal_record(record)
                    WAL_REPLAYED_TOTAL.labels(kind=record.kind).inc()
            except (GapDetectedError,) as exc:
                self._paused_instruments.add(instrument_id)
                log.critical(
                    "bar_aggregator.wal_replay_gap_paused",
                    instrument_id=instrument_id,
                    exc_info=exc,
                )

    async def _scan_wal_stream_keys(self) -> AsyncIterator[str]:
        pattern = f"wal:bar_aggregator:{self._settings.aggregator_shard}:[0-9]*"
        async for raw_key in self._require_redis().scan_iter(match=pattern):
            yield _decode_text(raw_key)

    async def _consume_quotes(self) -> None:
        pubsub = self._require_redis().pubsub()
        self._pubsub = pubsub
        await pubsub.psubscribe("quote.*")
        self._consumer_ready.set()
        async for message in pubsub.listen():
            if self._stop_event.is_set():
                break
            if message.get("type") != "pmessage":
                continue
            try:
                await self._handle_quote_message(message)
            except (Exception,) as exc:
                CONSUMER_ERRORS_TOTAL.inc()
                log.warning("bar_aggregator.consumer_message_error", exc_info=exc)

    async def _handle_quote_message(self, message: Mapping[str, Any]) -> None:
        payload = _decode_json_payload(message["data"])
        instrument_id = int(payload["instrument_id"])
        if instrument_id % self._settings.aggregator_shard_count != self._settings.aggregator_shard:
            return
        if instrument_id in self._paused_instruments:
            return

        source = str(payload["source"])
        ts = _parse_ts(str(payload["ts"]))
        kind = str(payload["kind"])
        record = WALTickRecord(
            entry_id="",
            instrument_id=instrument_id,
            source=source,
            ts=ts,
            price=_optional_decimal(payload.get("price")),
            volume=_optional_decimal(payload.get("volume")),
            bid=_optional_decimal(payload.get("bid")),
            ask=_optional_decimal(payload.get("ask")),
            kind=kind,
        )
        entry_id = await self._require_wal().append(record)
        self._apply_wal_record(record)
        bucket_start = ts.replace(microsecond=0)
        self._wal_entry_id_by_bucket[(instrument_id, bucket_start)] = entry_id
        self._publish_partial_if_present(instrument_id, bucket_start)
        TICKS_CONSUMED_TOTAL.labels(source=source).inc()
        IDLE_SECONDS.labels(instrument=str(instrument_id)).set(0.0)

    def _apply_wal_record(self, record: WALTickRecord) -> None:
        engine = self._require_engine()
        if record.kind == "tick":
            engine.on_tick(
                record.instrument_id,
                record.source,
                record.ts,
                record.price or Decimal("0"),
                record.volume or Decimal("0"),
            )
        elif record.kind == "quote":
            engine.on_quote(
                record.instrument_id,
                record.source,
                record.ts,
                record.bid or Decimal("0"),
                record.ask or Decimal("0"),
            )
        else:
            log.warning(
                "bar_aggregator.wal_replay_unknown_kind",
                kind=record.kind,
                instrument_id=record.instrument_id,
            )

    def _publish_partial_if_present(self, instrument_id: int, bucket_start: dt.datetime) -> None:
        bucket = self._require_engine().peek_bucket(instrument_id, bucket_start)
        if bucket is None:
            return
        canonical_id = self._canonical_id_lookup.get(instrument_id, str(instrument_id))
        self._require_bar_pubsub().update(
            self._snapshot(
                canonical_id=canonical_id,
                instrument_id=instrument_id,
                bucket_start=bucket_start,
                bucket=bucket,
                partial=True,
            )
        )

    async def _start_http(self) -> None:
        app = web.Application()
        app.add_routes(
            [
                web.get("/healthz", self._healthz),
                web.get("/metrics", self._metrics),
            ]
        )
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        self._http_site = web.TCPSite(
            self._http_runner,
            self._test_overrides.get("http_host", "0.0.0.0"),
            self._settings.http_port,
        )
        await self._http_site.start()

    async def _healthz(self, _request: web.Request) -> web.Response:
        flush_lag = cast(Any, FLUSH_LAG_SECONDS)._value.get()
        pg_unreachable = cast(Any, PG_UNREACHABLE_SECONDS)._value.get()
        healthy = flush_lag < 10 and pg_unreachable < 300
        status = 200 if healthy else 503
        return web.json_response(
            {"status": "ok", "paused_instruments": len(self._paused_instruments)},
            status=status,
        )

    async def _metrics(self, _request: web.Request) -> web.Response:
        return web.Response(body=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    async def _refresh_canonical_ids_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=300)
            except (asyncio.TimeoutError,) as exc:
                del exc
                await self._refresh_canonical_ids()

    async def _refresh_canonical_ids(self) -> None:
        async with self._require_pg_pool().acquire() as connection:
            rows = await connection.fetch(_INSTRUMENTS_SQL)
        self._canonical_id_lookup.clear()
        self._canonical_id_lookup.update({int(row["id"]): str(row["canonical_id"]) for row in rows})
        self._canonical_id_refreshed_at = time.monotonic()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except (NotImplementedError, RuntimeError) as exc:
                log.warning(
                    "bar_aggregator.signal_handler_unavailable",
                    signal=sig.name,
                    exc_info=exc,
                )

    def _pop_wal_entry_id(self, instrument_id: int, bucket_start: dt.datetime) -> str | None:
        return self._wal_entry_id_by_bucket.pop((instrument_id, bucket_start), None)

    def _snapshot(
        self,
        *,
        canonical_id: str,
        instrument_id: int,
        bucket_start: dt.datetime,
        bucket: BucketState,
        partial: bool,
    ) -> BarSnapshot:
        return BarSnapshot(
            canonical_id=canonical_id,
            instrument_id=instrument_id,
            tf="1s",
            bucket_start=bucket_start,
            open=bucket.open,
            high=bucket.high,
            low=bucket.low,
            close=bucket.close,
            volume=bucket.volume,
            volume_source=bucket.volume_source,
            trade_count=bucket.trade_count,
            revision=int(time.time_ns() // 1_000_000),
            partial=partial,
        )

    def _instrument_id_from_wal_key(self, key: str) -> int:
        return int(key.rsplit(":", maxsplit=1)[1])

    def _require_redis(self) -> redis_async.Redis:
        return _require(self._redis, "redis")

    def _require_pg_pool(self) -> asyncpg.Pool:
        return _require(self._pg_pool, "pg_pool")

    def _require_wal(self) -> WAL:
        return _require(self._wal, "wal")

    def _require_bar_pubsub(self) -> BarPubSub:
        return _require(self._bar_pubsub, "bar_pubsub")

    def _require_engine(self) -> AggregatorEngine:
        return _require(self._engine, "engine")

    def _require_flusher(self) -> Flusher:
        return _require(self._flusher, "flusher")

    def _require_minute_emitter(self) -> MinuteEmitter:
        return _require(self._minute_emitter, "minute_emitter")


def main() -> None:
    """Load settings, install uvloop, and run the bar aggregator lifecycle."""

    settings = Settings.from_env()
    uvloop.install()
    asyncio.run(AggregatorApp(settings).run_forever())


def _decode_json_payload(value: Any) -> Mapping[str, Any]:
    decoded = _decode_text(value)
    payload = json.loads(decoded)
    return cast(Mapping[str, Any], payload)


def _parse_ts(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return cast(str, value)


def _require(value: _T | None, name: str) -> _T:
    if value is None:
        raise RuntimeError(f"{name} is not initialized")
    return value


if __name__ == "__main__":
    main()
