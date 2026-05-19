from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from app.bot.base import BarEvent
from app.core import metrics

logger = structlog.get_logger(__name__)

_INTRADAY_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}
_LATE_TICK_GRACE_SECONDS = 2.0


def _bar_boundary_utc(ts: datetime, timeframe: str) -> datetime:
    """Return the UTC start of the bar containing ts for intraday timeframes."""
    epoch = ts.timestamp()
    period = _INTRADAY_SECONDS[timeframe]
    bar_start = (epoch // period) * period
    return datetime.fromtimestamp(bar_start, tz=UTC)


class _Bar:
    """Mutable accumulator for in-progress bar."""

    def __init__(self, canonical_id: str, timeframe: str, boundary: datetime) -> None:
        self.canonical_id = canonical_id
        self.timeframe = timeframe
        self.boundary = boundary
        self._open: Decimal | None = None
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._close: Decimal | None = None
        self._volume: Decimal = Decimal(0)
        self._tick_count = 0

    def update(self, price: Decimal, volume: Decimal) -> None:
        if self._open is None:
            self._open = price
        self._high = max(self._high, price) if self._high is not None else price
        self._low = min(self._low, price) if self._low is not None else price
        self._close = price
        self._volume += volume
        self._tick_count += 1

    def to_event(self, ts: datetime) -> BarEvent:
        return BarEvent(
            canonical_id=self.canonical_id,
            timeframe=self.timeframe,
            open=self._open or Decimal(0),
            high=self._high or Decimal(0),
            low=self._low or Decimal(0),
            close=self._close or Decimal(0),
            volume=self._volume,
            ts=ts,
        )

    @property
    def has_ticks(self) -> bool:
        return self._tick_count > 0

    @property
    def started_after_unpause(self) -> bool:
        return self._open is not None


class BarAggregator:
    """Converts ticks from Redis pubsub into OHLCV bars for one canonical_id."""

    def __init__(
        self,
        canonical_id: str,
        timeframe: str,
        queue: asyncio.Queue,  # type: ignore[type-arg]
        bot_id: str,
    ) -> None:
        self._canonical_id = canonical_id
        self._timeframe = timeframe
        self._queue = queue
        self._bot_id = bot_id
        self._paused = True
        self._current_bar: _Bar | None = None
        self._current_boundary: datetime | None = None
        self._unpaused_at: datetime | None = None
        self._now_override: datetime | None = None

    def unpause(self) -> None:
        self._paused = False
        self._unpaused_at = self._now()

    def _now(self) -> datetime:
        return self._now_override or datetime.now(tz=UTC)

    async def process_tick(self, raw: dict[str, Any]) -> None:
        if self._timeframe not in _INTRADAY_SECONDS:
            return

        price = Decimal(str(raw["price"]))
        volume = Decimal(str(raw.get("volume", "0")))
        ts = datetime.fromisoformat(str(raw["ts"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        boundary = _bar_boundary_utc(ts, self._timeframe)
        period_secs = _INTRADAY_SECONDS[self._timeframe]
        bar_close_ts = datetime.fromtimestamp(boundary.timestamp() + period_secs, tz=UTC)
        # Drop only ticks whose bar has already closed AND wall clock is past close+grace.
        # Use tick ts as "receipt time" proxy when no wall-clock override is set.
        receipt_time = self._now_override if self._now_override is not None else ts
        if (receipt_time - bar_close_ts).total_seconds() > _LATE_TICK_GRACE_SECONDS:
            metrics.bot_ticks_dropped_late_total.labels(bot_id=self._bot_id).inc()
            logger.debug("late_tick_dropped", canonical_id=self._canonical_id, ts=ts.isoformat())
            return

        if self._current_boundary is None:
            self._current_boundary = boundary
            self._current_bar = _Bar(self._canonical_id, self._timeframe, boundary)

        if boundary != self._current_boundary:
            completed = self._current_bar
            self._current_boundary = boundary
            self._current_bar = _Bar(self._canonical_id, self._timeframe, boundary)

            if (
                not self._paused
                and completed is not None
                and completed.started_after_unpause
                and completed.has_ticks
            ):
                bar_event = completed.to_event(ts=bar_close_ts)
                await self._emit(bar_event)
            elif (
                completed is not None
                and completed.has_ticks
                and not completed.started_after_unpause
            ):
                metrics.bot_partial_bars_skipped_total.labels(bot_id=self._bot_id).inc()

        if self._current_bar is not None and not self._paused:
            self._current_bar.update(price, volume)

    async def _emit(self, bar: BarEvent) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                metrics.bot_bar_events_dropped_total.labels(bot_id=self._bot_id).inc()
                logger.warning("bar_queue_overflow_drop_oldest", bot_id=self._bot_id)
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(bar)
        metrics.bot_bars_processed_total.labels(
            bot_id=self._bot_id, timeframe=self._timeframe
        ).inc()
