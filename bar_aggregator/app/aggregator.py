"""In-memory one-second bar aggregation state."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict, Unpack

from prometheus_client import Counter

VolumeSource = Literal["tape", "quote_proxy", "none"]

ENGINE_INSTRUMENTS_CAP_HIT_TOTAL = Counter(
    "bar_aggregator_engine_instruments_cap_hit_total",
    "Ticks or quotes rejected because the engine instrument cap was reached.",
)


class BucketFields(TypedDict, total=False):
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    volume_source: VolumeSource
    trade_count: int


@dataclass
class BucketState:
    bucket_start: datetime
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    volume_source: VolumeSource = "none"
    trade_count: int = 0

    def apply_tick(self, price: Decimal, volume: Decimal) -> None:
        if self.open is None:
            self.open = price

        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.close = price
        self.volume = (self.volume or Decimal("0")) + volume
        self.trade_count += 1

        if volume > Decimal("0") or self.volume_source == "quote_proxy":
            self.volume_source = "tape"

    def apply_quote(self, bid: Decimal, ask: Decimal) -> None:
        mid = (bid + ask) / Decimal("2")

        if self.open is None:
            self.open = mid
        if self.volume_source == "none":
            self.volume_source = "quote_proxy"

        self.high = mid if self.high is None else max(self.high, mid)
        self.low = mid if self.low is None else min(self.low, mid)
        self.close = mid


@dataclass
class AggregatorEngine:
    max_instruments: InitVar[int] = 1000
    buckets: dict[tuple[int, str], dict[datetime, BucketState]] = field(default_factory=dict)
    _max_instruments: int = field(init=False)
    _last_updated: dict[tuple[int, str, datetime], int] = field(default_factory=dict)
    _update_sequence: int = 0

    def __post_init__(self, max_instruments: int) -> None:
        self._max_instruments = max_instruments

    def on_tick(
        self,
        instrument_id: int,
        source: str,
        ts: datetime,
        price: Decimal,
        volume: Decimal,
    ) -> None:
        bucket = self._get_or_create_bucket(instrument_id, source, ts.replace(microsecond=0))
        if bucket is None:
            return

        bucket.apply_tick(price, volume)
        self._mark_updated(instrument_id, source, bucket.bucket_start)

    def on_quote(
        self,
        instrument_id: int,
        source: str,
        ts: datetime,
        bid: Decimal,
        ask: Decimal,
    ) -> None:
        bucket = self._get_or_create_bucket(instrument_id, source, ts.replace(microsecond=0))
        if bucket is None:
            return

        bucket.apply_quote(bid, ask)
        self._mark_updated(instrument_id, source, bucket.bucket_start)

    def peek_bucket(self, instrument_id: int, bucket_start: datetime) -> BucketState | None:
        latest_bucket: BucketState | None = None
        latest_sequence = -1

        for (stored_instrument_id, source), source_buckets in self.buckets.items():
            if stored_instrument_id != instrument_id:
                continue
            bucket = source_buckets.get(bucket_start)
            if bucket is None:
                continue

            sequence = self._last_updated.get((instrument_id, source, bucket_start), -1)
            if sequence > latest_sequence:
                latest_bucket = bucket
                latest_sequence = sequence

        return latest_bucket

    def apply_test_bucket(
        self,
        instrument_id: int,
        source: str,
        bucket_start: datetime,
        **fields: Unpack[BucketFields],
    ) -> BucketState:
        bucket = BucketState(bucket_start=bucket_start, **fields)
        self.buckets.setdefault((instrument_id, source), {})[bucket_start] = bucket
        self._mark_updated(instrument_id, source, bucket_start)
        return bucket

    def remove_bucket(self, instrument_id: int, source: str, bucket_start: datetime) -> None:
        """Public: remove bucket from buckets and last_updated. Idempotent."""
        source_buckets = self.buckets.get((instrument_id, source))
        if source_buckets is not None:
            source_buckets.pop(bucket_start, None)
            if not source_buckets:
                self.buckets.pop((instrument_id, source), None)
        self._last_updated.pop((instrument_id, source, bucket_start), None)

    def _get_or_create_bucket(
        self,
        instrument_id: int,
        source: str,
        bucket_start: datetime,
    ) -> BucketState | None:
        if (
            (instrument_id, source) not in self.buckets
            and len(self.buckets) >= self._max_instruments
        ):
            ENGINE_INSTRUMENTS_CAP_HIT_TOTAL.inc()
            return None

        source_buckets = self.buckets.setdefault((instrument_id, source), {})
        bucket = source_buckets.get(bucket_start)
        if bucket is None:
            bucket = BucketState(bucket_start=bucket_start)
            source_buckets[bucket_start] = bucket
        return bucket

    def _mark_updated(self, instrument_id: int, source: str, bucket_start: datetime) -> None:
        self._update_sequence += 1
        self._last_updated[(instrument_id, source, bucket_start)] = self._update_sequence
