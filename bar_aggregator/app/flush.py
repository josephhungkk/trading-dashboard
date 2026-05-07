"""Closed-bucket Postgres flush loop for the bar aggregator."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Callable, TypeAlias

import asyncpg  # type: ignore[import-untyped]
import structlog
from prometheus_client import Counter, Gauge

from bar_aggregator.app.aggregator import AggregatorEngine, BucketState
from bar_aggregator.app.bar_pubsub import BarPubSub, BarSnapshot, FINAL_REVISION
from bar_aggregator.app.wal import WAL

log = structlog.get_logger(__name__)

FLUSH_LAG_SECONDS = Gauge(
    "bar_aggregator_flush_lag_seconds",
    "Seconds since the most recent successful flush completed.",
)
PG_UNREACHABLE_SECONDS = Gauge(
    "bar_aggregator_pg_unreachable_seconds",
    "Seconds since flush has been paused due to PG PostgresError.",
)
BUCKETS_FLUSHED_TOTAL = Counter(
    "bar_aggregator_buckets_flushed_total",
    "Closed buckets inserted into bars_1s.",
    ["source"],
)

_FlushRow: TypeAlias = tuple[
    int,
    dt.datetime,
    str,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    str,
    int,
]
_FlushCandidate: TypeAlias = tuple[int, str, dt.datetime, BucketState, _FlushRow]

_INSERT_1S_SQL = """
INSERT INTO bars_1s (instrument_id, bucket_start, source,
                     open, high, low, close, volume, volume_source, trade_count)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (instrument_id, bucket_start) DO NOTHING
"""


class Flusher:
    def __init__(
        self,
        engine: AggregatorEngine,
        wal: WAL,
        bar_pubsub: BarPubSub,
        pg_pool: asyncpg.Pool,
        canonical_id_lookup: Mapping[int, str],
        flush_interval_ms: int = 1000,
        wal_entry_id_resolver: Callable[[int, dt.datetime], str | None] | None = None,
    ) -> None:
        self._engine = engine
        self._wal = wal
        self._bar_pubsub = bar_pubsub
        self._pg_pool = pg_pool
        self._canonical_id_lookup = canonical_id_lookup
        self._flush_interval_ms = flush_interval_ms
        self._wal_entry_id_resolver = wal_entry_id_resolver
        self._paused_since: dt.datetime | None = None
        self._last_flush_at: dt.datetime | None = None

    async def flush_once(self, now: dt.datetime | None = None) -> int:
        """Flush all closed buckets and return the number of rows written."""

        flush_now = now or dt.datetime.now(tz=dt.UTC)
        candidates = self._closed_candidates(flush_now)
        if not candidates:
            return 0

        records = [candidate[4] for candidate in candidates]
        try:
            async with self._pg_pool.acquire() as connection:
                async with connection.transaction():
                    await connection.executemany(_INSERT_1S_SQL, records)
        except (asyncpg.PostgresError,) as exc:
            if self._paused_since is None:
                self._paused_since = flush_now
            PG_UNREACHABLE_SECONDS.set((flush_now - self._paused_since).total_seconds())
            log.warning(
                "bar_aggregator.flush.pg_unreachable",
                bucket_count=len(records),
                paused_since=self._paused_since.isoformat(),
                exc_info=exc,
            )
            return 0

        for instrument_id, source, bucket_start, bucket, _record in candidates:
            self._remove_bucket(instrument_id, source, bucket_start)
            await self._ack_flushed(instrument_id, bucket_start)
            await self._publish_final(instrument_id, bucket_start, bucket)
            BUCKETS_FLUSHED_TOTAL.labels(source=source).inc()

        self._mark_success(flush_now)
        return len(records)

    async def flush_loop(self, *, stop: asyncio.Event) -> None:
        """Run flush_once every flush_interval_ms until stop is set."""

        timeout_seconds = self._flush_interval_ms / 1000
        while not stop.is_set():
            try:
                await self.flush_once()
            except (Exception,) as exc:
                log.error("flush.loop.unhandled", exc_info=exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout_seconds)
            except (asyncio.TimeoutError,) as exc:
                del exc

    def _closed_candidates(self, now: dt.datetime) -> list[_FlushCandidate]:
        candidates: list[_FlushCandidate] = []
        for (instrument_id, source), source_buckets in self._engine.buckets.items():
            for bucket_start, bucket in source_buckets.items():
                if bucket_start + dt.timedelta(seconds=1) > now:
                    continue
                record: _FlushRow = (
                    instrument_id,
                    bucket_start,
                    source,
                    bucket.open,
                    bucket.high,
                    bucket.low,
                    bucket.close,
                    bucket.volume,
                    bucket.volume_source,
                    bucket.trade_count,
                )
                candidates.append((instrument_id, source, bucket_start, bucket, record))
        return candidates

    def _remove_bucket(self, instrument_id: int, source: str, bucket_start: dt.datetime) -> None:
        self._engine.remove_bucket(instrument_id, source, bucket_start)

    async def _ack_flushed(self, instrument_id: int, bucket_start: dt.datetime) -> None:
        if self._wal_entry_id_resolver is None:
            return

        last_entry_id = self._wal_entry_id_resolver(instrument_id, bucket_start)
        if last_entry_id is None:
            return

        await self._wal.ack_flushed(instrument_id, last_entry_id)
        await self._wal.set_last_flushed(instrument_id, bucket_start)

    async def _publish_final(
        self,
        instrument_id: int,
        bucket_start: dt.datetime,
        bucket: BucketState,
    ) -> None:
        canonical_id = self._canonical_id_lookup.get(instrument_id)
        if canonical_id is None:
            log.warning("flush.publish_final.unknown_instrument", instrument_id=instrument_id)
            return

        snap = BarSnapshot(
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
            revision=FINAL_REVISION,
            partial=False,
        )
        await self._bar_pubsub.publish_final(snap)

    def _mark_success(self, now: dt.datetime) -> None:
        self._paused_since = None
        PG_UNREACHABLE_SECONDS.set(0)

        if self._last_flush_at is None:
            FLUSH_LAG_SECONDS.set(0)
        else:
            FLUSH_LAG_SECONDS.set(max((now - self._last_flush_at).total_seconds(), 0))
        self._last_flush_at = now

    def flush_lag_seconds(self) -> float:
        if self._last_flush_at is None:
            return 0.0
        return (dt.datetime.now(tz=dt.UTC) - self._last_flush_at).total_seconds()

    def pg_unreachable_seconds(self) -> float:
        if self._paused_since is None:
            return 0.0
        return (dt.datetime.now(tz=dt.UTC) - self._paused_since).total_seconds()
