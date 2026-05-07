"""Central metric registry for bar_aggregator service.

Re-exports metrics owned by other modules so a single import surface exists,
and defines the metrics owned by main.py here.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

from bar_aggregator.app.bar_pubsub import (
    CALLBACK_ERRORS,
    CHANNEL_CAP_HIT,
    PUBLISHES_TOTAL,
    TICKS_TOTAL,
)
from bar_aggregator.app.flush import (
    BUCKETS_FLUSHED_TOTAL,
    FLUSH_LAG_SECONDS,
    PG_UNREACHABLE_SECONDS,
)
from bar_aggregator.app.minute_emitter import MINUTE_BARS_EMITTED_TOTAL
from bar_aggregator.app.wal import (
    WAL_DEPTH_BYTES,
    WAL_LAG_SECONDS,
    WAL_TRUNCATED_TOTAL,
)

TICKS_CONSUMED_TOTAL = Counter(
    "bar_aggregator_ticks_consumed_total",
    "Quote-bus messages consumed.",
    ["source"],
)
WAL_REPLAYED_TOTAL = Counter(
    "bar_aggregator_wal_replayed_total",
    "WAL records replayed at startup.",
    ["kind"],
)
CONSUMER_ERRORS_TOTAL = Counter(
    "bar_aggregator_consumer_errors_total",
    "Quote-bus message handling errors.",
)
IDLE_SECONDS = Gauge(
    "bar_aggregator_idle_seconds",
    "Seconds since last quote per instrument.",
    ["instrument"],
)

__all__ = [
    "WAL_DEPTH_BYTES",
    "WAL_LAG_SECONDS",
    "WAL_TRUNCATED_TOTAL",
    "FLUSH_LAG_SECONDS",
    "PG_UNREACHABLE_SECONDS",
    "BUCKETS_FLUSHED_TOTAL",
    "PUBLISHES_TOTAL",
    "TICKS_TOTAL",
    "CHANNEL_CAP_HIT",
    "CALLBACK_ERRORS",
    "MINUTE_BARS_EMITTED_TOTAL",
    "TICKS_CONSUMED_TOTAL",
    "WAL_REPLAYED_TOTAL",
    "CONSUMER_ERRORS_TOTAL",
    "IDLE_SECONDS",
]
