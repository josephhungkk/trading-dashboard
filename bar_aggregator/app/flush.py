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
    "Seconds since flush has been paused due to PG OperationalError.",
)
BUCKETS_FLUSHED_TOTAL = Counter(
    "bar_aggregator_buckets_flushed_total",
    "Closed buckets COPY'd into bars_1s.",
    ["source"],
)

_BucketKey: TypeAlias = tuple[int, str]
_FlushRow: TypeAlias = tuple[
    int,
    dt.datetime,
    str,
    int,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    str,
    int,
]
_FlushCandidate: TypeAlias = tuple[int, str, dt.datetime, BucketState, _FlushRow]

_BARS_1S_COLUMNS = [
    "instrument_id",
    "bucket_start",
    "source",
    "source_priority",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "volume_source",
    "trade_count",
]


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
        self._last_successful_flush_at: dt.datetime | None = None

    async def flush_once(self, now: dt.datetime | None = None) -> int:
        """Flush all closed buckets and return the number of rows written."""

        flush_now = now or dt.datetime.now(tz=dt.UTC)
        candidates = self._closed_candidates(flush_now)
        if not candidates:
            self._mark_success(flush_now)
            return 0

        records = [candidate[4] for candidate in candidates]
        try:
            async with self._pg_pool.acquire() as connection:
                await connection.copy_records_to_table(
                    "bars_1s",
                    records=records,
                    columns=_BARS_1S_COLUMNS,
                )
        except (asyncpg.exceptions.OperationalError,) as exc:
            if self._paused_since is None:
                self._paused_since = flush_now
            PG_UNREACHABLE_SECONDS.inc(self._flush_interval_ms / 1000)
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
            await self.flush_once()
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
                    99,
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
        key: _BucketKey = (instrument_id, source)
        source_buckets = self._engine.buckets.get(key)
        if source_buckets is None:
            return

        source_buckets.pop(bucket_start, None)
        if not source_buckets:
            self._engine.buckets.pop(key, None)

    async def _ack_flushed(self, instrument_id: int, bucket_start: dt.datetime) -> None:
        if self._wal_entry_id_resolver is None:
            return

        last_entry_id = self._wal_entry_id_resolver(instrument_id, bucket_start)
        if last_entry_id is None:
            return

        await self._wal.ack_flushed(instrument_id, last_entry_id)

    async def _publish_final(
        self,
        instrument_id: int,
        bucket_start: dt.datetime,
        bucket: BucketState,
    ) -> None:
        canonical_id = self._canonical_id_lookup[instrument_id]
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

        if self._last_successful_flush_at is None:
            FLUSH_LAG_SECONDS.set(0)
        else:
            FLUSH_LAG_SECONDS.set(max((now - self._last_successful_flush_at).total_seconds(), 0))
        self._last_successful_flush_at = now
