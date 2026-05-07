"""Redis Streams write-ahead log for bar aggregator ticks.

The stream MAXLEN cap bounds per-instrument WAL growth. Trimming is only done
after a Postgres flush acknowledgement, so replay starts from unacked entries.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any, cast

import redis.asyncio as redis_async  # type: ignore[import-untyped]
import structlog
from prometheus_client import Counter, Gauge

log = structlog.get_logger(__name__)

WAL_DEPTH_BYTES = Gauge(
    "bar_aggregator_wal_depth_bytes",
    "Approximate Redis memory usage for bar aggregator WAL streams.",
)
WAL_LAG_SECONDS = Gauge(
    "bar_aggregator_wal_lag_seconds",
    "Seconds since the last flushed WAL timestamp.",
    ["instrument"],
)
WAL_TRUNCATED_TOTAL = Counter(
    "bar_aggregator_wal_truncated_total",
    "WAL replay detected a gap beyond the configured safety bound.",
    ["instrument"],
)


class GapDetectedError(RuntimeError):
    """Raised when the oldest WAL entry's lag versus last-flushed exceeds the safety bound."""


@dataclasses.dataclass(frozen=True)
class WALTickRecord:
    entry_id: str
    instrument_id: int
    source: str
    ts: dt.datetime
    price: Decimal | None
    volume: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    kind: str  # "tick" | "quote"


class WAL:
    def __init__(
        self,
        redis: redis_async.Redis,
        shard: int,
        flush_interval_ms: int,
        max_len: int = 50_000,
    ) -> None:
        self._redis = redis
        self._shard = shard
        self._flush_interval_ms = flush_interval_ms
        self._max_len = max_len

    async def append(self, record: WALTickRecord) -> str:
        fields = {
            "kind": record.kind,
            "instrument_id": str(record.instrument_id),
            "source": record.source,
            "ts": _to_utc(record.ts).isoformat(),
            "price": _decimal_to_field(record.price),
            "volume": _decimal_to_field(record.volume),
            "bid": _decimal_to_field(record.bid),
            "ask": _decimal_to_field(record.ask),
        }
        entry_id = await self._redis.xadd(
            self._stream_key(record.instrument_id),
            fields,
            maxlen=self._max_len,
            approximate=True,
        )
        return _decode_text(entry_id)

    async def ack_flushed(self, instrument_id: int, last_entry_id: str) -> None:
        await self._redis.xtrim(
            self._stream_key(instrument_id),
            minid=last_entry_id,
            approximate=False,
        )

    async def set_last_flushed(self, instrument_id: int, ts: dt.datetime) -> None:
        await self._redis.hset(
            self._flushed_ts_key(),
            str(instrument_id),
            _to_utc(ts).isoformat(),
        )

    async def get_last_flushed(self, instrument_id: int) -> dt.datetime | None:
        value = await self._redis.hget(self._flushed_ts_key(), str(instrument_id))
        if value is None:
            return None
        return _parse_ts(_decode_text(value))

    async def replay(self, instrument_id: int | None = None) -> AsyncIterator[WALTickRecord]:
        stream_keys: list[str]
        if instrument_id is None:
            stream_keys = []
            async for key in self._redis.scan_iter(match=self._stream_scan_match()):
                stream_keys.append(_decode_text(key))
        else:
            stream_keys = [self._stream_key(instrument_id)]

        for key in stream_keys:
            key_instrument_id = self._instrument_id_from_key(key)
            await self._raise_on_gap(key, key_instrument_id)
            entries = await self._redis.xrange(key, min="-", max="+")
            for entry_id, raw_fields in entries:
                yield _record_from_entry(entry_id, raw_fields)

    async def wal_lag_seconds(self, instrument_id: int) -> float:
        last_flushed = await self.get_last_flushed(instrument_id)
        if last_flushed is None:
            WAL_LAG_SECONDS.labels(instrument=str(instrument_id)).set(0.0)
            return 0.0

        lag_seconds = max((_utc_now() - last_flushed).total_seconds(), 0.0)
        WAL_LAG_SECONDS.labels(instrument=str(instrument_id)).set(lag_seconds)
        return lag_seconds

    async def wal_depth_bytes(self) -> int:
        depth_bytes = 0
        async for key in self._redis.scan_iter(match=self._stream_scan_match()):
            usage = await self._redis.memory_usage(key)
            if usage is not None:
                depth_bytes += int(usage)

        WAL_DEPTH_BYTES.set(depth_bytes)
        return depth_bytes

    async def _raise_on_gap(self, key: str, instrument_id: int) -> None:
        oldest_entries = await self._redis.xrange(key, min="-", max="+", count=1)
        if not oldest_entries:
            return

        _entry_id, raw_fields = oldest_entries[0]
        oldest_record = _record_from_entry(_entry_id, raw_fields)
        last_flushed = await self.get_last_flushed(instrument_id)
        if last_flushed is None:
            return

        lag_seconds = (oldest_record.ts - last_flushed).total_seconds()
        max_lag_seconds = (2 * self._flush_interval_ms) / 1000
        if lag_seconds <= max_lag_seconds:
            return

        WAL_TRUNCATED_TOTAL.labels(instrument=str(instrument_id)).inc()
        log.critical(
            "bar_aggregator.wal_gap_detected",
            instrument_id=instrument_id,
            shard=self._shard,
            key=key,
            oldest_wal_ts=oldest_record.ts.isoformat(),
            last_flushed_ts=last_flushed.isoformat(),
            lag_seconds=lag_seconds,
            max_lag_seconds=max_lag_seconds,
        )
        raise GapDetectedError(
            "WAL gap detected for instrument "
            f"{instrument_id}: lag {lag_seconds:.3f}s exceeds {max_lag_seconds:.3f}s"
        )

    def _stream_key(self, instrument_id: int) -> str:
        return f"wal:bar_aggregator:{self._shard}:{instrument_id}"

    def _stream_scan_match(self) -> str:
        return f"wal:bar_aggregator:{self._shard}:[0-9]*"

    def _flushed_ts_key(self) -> str:
        return f"wal:bar_aggregator:{self._shard}:flushed_ts"

    def _instrument_id_from_key(self, key: str) -> int:
        return int(key.rsplit(":", maxsplit=1)[1])


def _record_from_entry(entry_id: Any, raw_fields: Mapping[Any, Any]) -> WALTickRecord:
    fields = {_decode_text(key): _decode_text(value) for key, value in raw_fields.items()}
    return WALTickRecord(
        entry_id=_decode_text(entry_id),
        instrument_id=int(fields["instrument_id"]),
        source=fields["source"],
        ts=_parse_ts(fields["ts"]),
        price=_field_to_decimal(fields.get("price", "")),
        volume=_field_to_decimal(fields.get("volume", "")),
        bid=_field_to_decimal(fields.get("bid", "")),
        ask=_field_to_decimal(fields.get("ask", "")),
        kind=fields["kind"],
    )


def _decimal_to_field(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value)


def _field_to_decimal(value: str) -> Decimal | None:
    if value == "":
        return None
    return Decimal(value)


def _to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _parse_ts(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    return _to_utc(parsed)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return cast(str, value)
