"""Emit closed one-minute bars from in-memory one-second buckets."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]
import structlog
from prometheus_client import Counter

from bar_aggregator.app.aggregator import AggregatorEngine, BucketState, VolumeSource
from bar_aggregator.app.bar_pubsub import BarPubSub, BarSnapshot, FINAL_REVISION

log = structlog.get_logger(__name__)

MINUTE_BARS_EMITTED_TOTAL = Counter(
    "bar_aggregator_minute_bars_emitted_total",
    "1m bars UPSERT'd into bars_1m by minute_emitter (priority 99).",
    ["source"],
)

AGGREGATOR_PRIORITY: int = 99

_UPSERT_1M_SQL = """
INSERT INTO bars_1m (instrument_id, bucket_start, source, source_priority,
                     open, high, low, close, volume, trade_count, volume_source)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (instrument_id, bucket_start) DO UPDATE
  SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
      close=EXCLUDED.close, volume=EXCLUDED.volume,
      trade_count=EXCLUDED.trade_count,
      source=EXCLUDED.source, source_priority=EXCLUDED.source_priority,
      volume_source=EXCLUDED.volume_source
  WHERE EXCLUDED.source_priority < bars_1m.source_priority
"""


@dataclass(frozen=True)
class _MinuteBar:
    instrument_id: int
    source: str
    bucket_start: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    trade_count: int
    volume_source: VolumeSource


_EmittedBar = tuple[BarSnapshot, str]


class MinuteEmitter:
    def __init__(
        self,
        engine: AggregatorEngine,
        bar_pubsub: BarPubSub,
        pg_pool: asyncpg.Pool,
        canonical_id_lookup: Mapping[int, str],
    ) -> None:
        self._engine = engine
        self._bar_pubsub = bar_pubsub
        self._pg_pool = pg_pool
        self._canonical_id_lookup = canonical_id_lookup

    async def tick(self, minute_close: dt.datetime | None = None) -> int:
        """Emit 1m bars for the minute that closed at `minute_close`."""
        close_at = (minute_close or dt.datetime.now(tz=dt.UTC)).replace(
            second=0,
            microsecond=0,
        )
        window_start = close_at - dt.timedelta(seconds=60)
        bars = self._collect_minute_bars(window_start=window_start, minute_close=close_at)

        if not bars:
            return 0

        emitted: list[_EmittedBar] = []
        try:
            async with self._pg_pool.acquire() as conn:
                async with conn.transaction():
                    for bar in bars:
                        await conn.execute(
                            _UPSERT_1M_SQL,
                            bar.instrument_id,
                            bar.bucket_start,
                            f"aggregator-{bar.source}",
                            AGGREGATOR_PRIORITY,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume,
                            bar.trade_count,
                            bar.volume_source,
                        )
                        snap = self._snapshot(bar)
                        if snap is not None:
                            emitted.append((snap, bar.source))
        except (asyncpg.exceptions.OperationalError,) as exc:
            log.warning("minute_emitter.pg_unreachable", exc_info=exc)
            return 0

        published = 0
        for snap, source in emitted:
            try:
                await self._bar_pubsub.publish_final(snap)
                MINUTE_BARS_EMITTED_TOTAL.labels(source=source).inc()
                published += 1
            except (Exception,) as exc:
                log.warning(
                    "minute_emitter.publish_final.failed",
                    instrument_id=snap.instrument_id,
                    exc_info=exc,
                )

        return published

    async def run_loop(self, *, stop: asyncio.Event) -> None:
        """Sleep to next minute boundary, call tick(), repeat until stop is set."""
        while not stop.is_set():
            now = dt.datetime.now(tz=dt.UTC)
            next_min = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
            timeout = max(0.0, (next_min - now).total_seconds())

            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout)
                break
            except (asyncio.TimeoutError,) as exc:
                del exc
                await self.tick(minute_close=next_min)

    def _collect_minute_bars(
        self,
        *,
        window_start: dt.datetime,
        minute_close: dt.datetime,
    ) -> list[_MinuteBar]:
        bars: list[_MinuteBar] = []
        for (instrument_id, source), source_buckets in list(self._engine.buckets.items()):
            window_buckets = [
                bucket
                for bucket_start, bucket in source_buckets.items()
                if window_start <= bucket_start < minute_close
            ]
            if not window_buckets:
                continue

            bar = self._aggregate_buckets(
                instrument_id=instrument_id,
                source=source,
                bucket_start=window_start,
                buckets=window_buckets,
            )
            if bar is not None:
                bars.append(bar)

        return bars

    def _aggregate_buckets(
        self,
        *,
        instrument_id: int,
        source: str,
        bucket_start: dt.datetime,
        buckets: list[BucketState],
    ) -> _MinuteBar | None:
        ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)
        first = ordered[0]
        last = ordered[-1]
        open_price = first.open
        close_price = last.close
        highs = [bucket.high for bucket in ordered if bucket.high is not None]
        lows = [bucket.low for bucket in ordered if bucket.low is not None]

        if open_price is None or close_price is None or not highs or not lows:
            log.warning(
                "minute_emitter.incomplete_bucket_window",
                instrument_id=instrument_id,
                source=source,
                bucket_start=bucket_start.isoformat(),
            )
            return None

        volumes = [bucket.volume for bucket in ordered if bucket.volume is not None]
        volume = sum(volumes, Decimal("0")) if volumes else None

        return _MinuteBar(
            instrument_id=instrument_id,
            source=source,
            bucket_start=bucket_start,
            open=open_price,
            high=max(highs),
            low=min(lows),
            close=close_price,
            volume=volume,
            trade_count=sum(bucket.trade_count for bucket in ordered),
            volume_source=self._best_volume_source(ordered),
        )

    def _snapshot(self, bar: _MinuteBar) -> BarSnapshot | None:
        canonical_id = self._canonical_id_lookup.get(bar.instrument_id)
        if canonical_id is None:
            log.warning(
                "minute_emitter.missing_canonical_id",
                instrument_id=bar.instrument_id,
                source=bar.source,
            )
            return None

        return BarSnapshot(
            canonical_id=canonical_id,
            instrument_id=bar.instrument_id,
            tf="1m",
            bucket_start=bar.bucket_start,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            volume_source=bar.volume_source,
            trade_count=bar.trade_count,
            revision=FINAL_REVISION,
            partial=False,
        )

    @staticmethod
    def _best_volume_source(buckets: list[BucketState]) -> VolumeSource:
        volume_sources = {bucket.volume_source for bucket in buckets}
        if "tape" in volume_sources:
            return "tape"
        if "quote_proxy" in volume_sources:
            return "quote_proxy"
        return "none"
