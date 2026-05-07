"""Bar snapshot pub/sub with per-channel partial publish coalescing."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
from decimal import Decimal
from typing import Any

import redis.asyncio as redis_async  # type: ignore[import-untyped]
import structlog
from prometheus_client import Counter

log = structlog.get_logger(__name__)

# Public sentinel: final (closed-bucket) publishes use this revision so any subscriber
# preferring "highest revision wins" treats them as monotonically last.
FINAL_REVISION: int = 2**31 - 1

PUBLISHES_TOTAL = Counter(
    "bar_aggregator_pubsub_publishes_total",
    "Total bar snapshot publishes by timeframe and kind.",
    ["tf", "kind"],
)
TICKS_TOTAL = Counter(
    "bar_aggregator_pubsub_ticks_total",
    "Total live-tail bar snapshot updates received by timeframe.",
    ["tf"],
)
CHANNEL_CAP_HIT = Counter(
    "bar_aggregator_pubsub_channel_cap_hit_total",
    "Total bar pub/sub channel updates dropped because the channel cap was reached.",
)
CALLBACK_ERRORS = Counter(
    "bar_aggregator_pubsub_callback_errors_total",
    "Total isolated callback errors raised by deferred bar pub/sub publishes.",
)


@dataclasses.dataclass(frozen=True)
class BarSnapshot:
    canonical_id: str
    instrument_id: int
    tf: str
    bucket_start: dt.datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    volume_source: str
    trade_count: int
    revision: int
    partial: bool


def _channel_name(snap: BarSnapshot) -> str:
    return f"bar.{snap.canonical_id}.{snap.tf}"


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _encode_snapshot(snap: BarSnapshot) -> bytes:
    payload = dataclasses.asdict(snap)
    return json.dumps(payload, default=_json_default, separators=(",", ":")).encode()


class _ChannelCoalescer:
    """One per (instrument_id, tf). Coalesces N ticks within 250ms into one publish."""

    def __init__(
        self,
        redis: redis_async.Redis,
        channel: str,
        max_interval_ms: int = 250,
    ) -> None:
        self._redis = redis
        self._channel = channel
        self._max_interval = max_interval_ms / 1000
        self._latest: BarSnapshot | None = None
        self._last_publish_at: float = 0.0
        self._task: asyncio.Task[None] | None = None

    def update(self, snap: BarSnapshot) -> None:
        self._latest = snap
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._deferred_publish())

    async def publish_final(self, snap: BarSnapshot) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._task is task:
                self._task = None

        self._latest = None
        final_snap = dataclasses.replace(snap, revision=FINAL_REVISION, partial=False)
        await self._publish(final_snap, kind="final")
        self._last_publish_at = asyncio.get_running_loop().time()

    async def _deferred_publish(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            delay = max(0.0, self._last_publish_at + self._max_interval - loop.time())
            if delay > 0:
                await asyncio.sleep(delay)

            snap = self._latest
            if snap is None:
                return

            self._latest = None
            await self._publish(snap, kind="partial")
            self._last_publish_at = loop.time()
        except (Exception,) as exc:
            CALLBACK_ERRORS.inc()
            log.warning(
                "bar_pubsub.deferred_publish_error",
                channel=self._channel,
                exc_info=exc,
            )
        finally:
            current_task = asyncio.current_task()
            if self._task is current_task:
                self._task = None

    async def _publish(self, snap: BarSnapshot, *, kind: str) -> None:
        await self._redis.publish(self._channel, _encode_snapshot(snap))
        PUBLISHES_TOTAL.labels(tf=snap.tf, kind=kind).inc()


class BarPubSub:
    def __init__(
        self,
        redis: redis_async.Redis,
        max_interval_ms: int = 250,
        max_channels: int = 5000,
    ) -> None:
        self._redis = redis
        self._max_interval_ms = max_interval_ms
        self._max_channels = max_channels
        self._channels: dict[str, _ChannelCoalescer] = {}

    def update(self, snap: BarSnapshot) -> None:
        TICKS_TOTAL.labels(tf=snap.tf).inc()
        channel = _channel_name(snap)
        coalescer = self._channels.get(channel)
        if coalescer is None:
            if len(self._channels) >= self._max_channels:
                CHANNEL_CAP_HIT.inc()
                log.warning(
                    "bar_pubsub.channel_cap_hit",
                    channel=channel,
                    max_channels=self._max_channels,
                )
                return
            coalescer = _ChannelCoalescer(
                redis=self._redis,
                channel=channel,
                max_interval_ms=self._max_interval_ms,
            )
            self._channels[channel] = coalescer

        coalescer.update(snap)

    async def publish_final(self, snap: BarSnapshot) -> None:
        channel = _channel_name(snap)
        coalescer = self._channels.get(channel)
        if coalescer is None:
            if len(self._channels) >= self._max_channels:
                CHANNEL_CAP_HIT.inc()
                log.warning(
                    "bar_pubsub.channel_cap_hit",
                    channel=channel,
                    max_channels=self._max_channels,
                )
                return
            coalescer = _ChannelCoalescer(
                redis=self._redis,
                channel=channel,
                max_interval_ms=self._max_interval_ms,
            )
            self._channels[channel] = coalescer

        await coalescer.publish_final(snap)

    async def aclose(self) -> None:
        tasks = [
            coalescer._task
            for coalescer in self._channels.values()
            if coalescer._task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for coalescer in self._channels.values():
            if coalescer._task is not None and coalescer._task.done():
                coalescer._task = None
