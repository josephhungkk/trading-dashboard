"""BarService — historical bar fetch, cache-gap detection, cross-worker coalescing.

Phase 9 Chunk D implementation.  Skeleton fields (_SOURCE_PRIORITY, _priority_for_source,
ActiveSetRow, start/stop, active_set) are preserved verbatim from the Chunk C skeleton.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, NamedTuple

import asyncpg  # type: ignore[import-untyped]
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

logger = structlog.get_logger(__name__)

# ──────────────────────── Source priority map ────────────────────────────────

_SOURCE_PRIORITY: Final[Mapping[str, int]] = {
    "schwab": 1,
    "alpaca": 2,
    "ibkr": 3,
    "futu": 4,
    "aggregator-schwab": 99,
    "aggregator-alpaca": 99,
    "aggregator-ibkr": 99,
    "aggregator-futu": 99,
}

_DEFAULTS: Final[dict[str, list[str]]] = {
    "equity_us": ["schwab", "alpaca", "ibkr"],
    "equity_hk": ["futu", "ibkr"],
    "crypto": ["alpaca"],
    "fx": ["ibkr"],
}

# Timeframes that are >= 1 minute and can trigger backfill.
# Sub-minute timeframes (e.g. '1s') are cache-only.
_BACKFILL_ELIGIBLE_TF: Final[frozenset[str]] = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
)

# Timeframe → timedelta for cursor arithmetic in _fetch_with_chunks.
_TF_DELTA: Final[dict[str, timedelta]] = {
    "1s": timedelta(seconds=1),
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}

# Cross-worker in-process dedup: key=(instrument_id, tf, gap_start, gap_end)
_IN_FLIGHT: dict[tuple[int, str, datetime, datetime], asyncio.Event] = {}


def _priority_for_source(source: str) -> int:
    """Single chokepoint mapping source → source_priority for UPSERT WHERE clause."""
    if source not in _SOURCE_PRIORITY:
        raise ValueError(f"unknown bar source: {source!r}")
    return _SOURCE_PRIORITY[source]


def _tf_to_interval(tf: str) -> timedelta:
    """Convert a timeframe string to a timedelta for cursor arithmetic."""
    try:
        return _TF_DELTA[tf]
    except KeyError:
        raise ValueError(f"unknown timeframe: {tf!r}") from None


# ──────────────────────── Data classes ──────────────────────────────────────


class ActiveSetRow(NamedTuple):
    """Row from BarService.active_set(): one entry per active instrument."""

    instrument_id: int
    recency_score: int


@dataclass(frozen=True)
class Bar:
    """Single paginated bar row returned by BarService.get_bars."""

    instrument_id: int
    bucket_start: datetime
    source: str
    source_priority: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    volume_source: str
    trade_count: int


@dataclass(frozen=True)
class BarPage:
    """Paginated result from BarService.get_bars."""

    bars: list[Bar]
    next_cursor: str | None  # None → no more pages


# ──────────────────────── Exceptions ────────────────────────────────────────


class InstrumentNotFound(Exception):  # noqa: N818
    """Raised when canonical_id does not resolve to any instruments row."""


class BarFetchTooLarge(Exception):  # noqa: N818
    """Raised when the chunked fetch loop exceeds the 100-chunk hard cap."""


class InvalidCursor(Exception):  # noqa: N818
    """Raised when the cursor token is malformed or has unknown version."""


class BarSourceUnavailable(Exception):  # noqa: N818
    """Raised when no healthy sidecar can satisfy the requested asset class."""


# ──────────────────────── BarService ────────────────────────────────────────


class BarService:
    """Orchestrator for historical bar fetch, cache-gap detection, and cross-worker coalescing."""

    def __init__(self, registry: Any | None = None) -> None:
        self._started = False
        self._lock = asyncio.Lock()
        self._registry = registry  # BrokerRegistry | None; lazy-loaded on first use

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            logger.info("bar_service.start")
            self._started = True

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            logger.info("bar_service.stop")
            self._started = False

    async def active_set(self, session: AsyncSession) -> list[ActiveSetRow]:
        """Return up to 1000 instruments worth pre-warming.

        Active set = (positions UNION watchlist_entries UNION recent chart_layouts),
        deduped, ordered by recency_score DESC, capped at 1000 (matches the
        per-aggregator memory cap; sharding takes over above this — see spec
        §3 lines 396-417 + §4 line 509).
        """
        rows = (
            await session.execute(
                text(
                    """
                    WITH cfg AS (
                      SELECT value::int AS recency_days
                      FROM app_config
                      WHERE namespace = 'charts'
                        AND key = 'bar_active_set_recency_days'
                    )
                    SELECT instrument_id, MAX(recency_score) AS recency_score
                    FROM (
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM NOW())::bigint AS recency_score
                        FROM positions
                       WHERE instrument_id IS NOT NULL
                      UNION ALL
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM NOW())::bigint
                        FROM watchlist_entries
                       WHERE instrument_id IS NOT NULL
                      UNION ALL
                      SELECT instrument_id,
                             EXTRACT(EPOCH FROM updated_at)::bigint
                        FROM chart_layouts
                       WHERE updated_at >
                             NOW() - (SELECT recency_days FROM cfg) * INTERVAL '1 day'
                    ) sources
                    GROUP BY instrument_id
                    ORDER BY recency_score DESC
                    LIMIT 1000
                    """
                )
            )
        ).all()
        result = [
            ActiveSetRow(instrument_id=r.instrument_id, recency_score=r.recency_score) for r in rows
        ]
        logger.info("bar_service.active_set", count=len(result))
        return result

    # ──────────────────── Public API ────────────────────────────────────────

    async def get_bars(
        self,
        canonical_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
        cursor: str | None = None,
        *,
        session: AsyncSession,
    ) -> BarPage:
        """Fetch bars for [start, end), triggering backfill on cache miss for tf >= '1m'.

        Parameters
        ----------
        canonical_id:
            Instrument canonical identifier (e.g. ``"equity_us:AAPL:NASDAQ"``).
        timeframe:
            Bar timeframe (``"1m"``, ``"5m"``, ``"1h"``, etc.).
        start:
            Inclusive range start (UTC).
        end:
            Exclusive range end (UTC).
        limit:
            Maximum rows per page (hard cap 10000).
        cursor:
            Opaque pagination cursor from a previous call.  ``None`` = first page.
        session:
            Live async SQLAlchemy session (injected by caller / FastAPI route).
        """
        # 1. Resolve instrument
        instrument_id, asset_class = await self._resolve_instrument(canonical_id, session)

        # 2. Decode cursor (if provided) → derive effective end for WHERE clause
        cursor_ts: datetime | None = None
        if cursor is not None:
            cursor_ts = _decode_cursor(cursor)

        # 3. Sub-minute timeframes are cache-only: never backfill, never wait.
        table = "bars_1m" if timeframe in _BACKFILL_ELIGIBLE_TF else "bars_1s"
        if timeframe not in _BACKFILL_ELIGIBLE_TF:
            return await self._query_bars(
                instrument_id=instrument_id,
                table=table,
                start=start,
                end=end,
                limit=limit,
                cursor_ts=cursor_ts,
                session=session,
            )

        # 4. Detect gap: check whether bars_1m already covers [start, end).
        has_gap = await self._has_cache_gap(instrument_id, start, end, session)

        if has_gap:
            asset_class_key = _asset_class_to_priority_key(asset_class)
            source = await self._resolve_source(asset_class_key, session)

            # In-process coalescing: check _IN_FLIGHT dict first.
            gap_key = (instrument_id, timeframe, start, end)
            existing_event = _IN_FLIGHT.get(gap_key)
            if existing_event is not None:
                # Another coroutine in this process is already fetching — wait on event.
                logger.info(
                    "bar_service.in_process_wait",
                    canonical_id=canonical_id,
                    timeframe=timeframe,
                )
                t0 = time.monotonic()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.ensure_future(existing_event.wait())),
                        timeout=16.0,
                    )
                except TimeoutError:
                    logger.warning("bar_service.in_process_wait_timeout", canonical_id=canonical_id)
            else:
                # Cross-worker chokepoint: UPSERT bar_backfill_jobs.
                job_id, was_new = await self._upsert_backfill_job(
                    instrument_id=instrument_id,
                    source=source,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    session=session,
                )

                if was_new:
                    # This worker is the primary fetcher.
                    my_event: asyncio.Event = asyncio.Event()
                    _IN_FLIGHT[gap_key] = my_event
                    try:
                        await self._primary_fetch(
                            canonical_id=canonical_id,
                            instrument_id=instrument_id,
                            timeframe=timeframe,
                            start=start,
                            end=end,
                            source=source,
                            job_id=job_id,
                            session=session,
                        )
                        metrics.bar_service_backfill_total.labels(
                            source=source, timeframe=timeframe, outcome="done"
                        ).inc()
                    except Exception:
                        metrics.bar_service_backfill_total.labels(
                            source=source, timeframe=timeframe, outcome="failed"
                        ).inc()
                        raise
                    finally:
                        my_event.set()
                        _IN_FLIGHT.pop(gap_key, None)
                else:
                    # Another worker (different process) is fetching — wait via pg_notify.
                    metrics.bar_service_backfill_total.labels(
                        source=source, timeframe=timeframe, outcome="coalesced_wait"
                    ).inc()
                    t0 = time.monotonic()
                    await self._wait_for_job(job_id)
                    elapsed = time.monotonic() - t0
                    metrics.bar_service_cross_worker_wait_seconds.observe(elapsed)

        # 5. Return paginated rows.
        return await self._query_bars(
            instrument_id=instrument_id,
            table=table,
            start=start,
            end=end,
            limit=limit,
            cursor_ts=cursor_ts,
            session=session,
        )

    # ──────────────────── Internal helpers ──────────────────────────────────

    async def _resolve_instrument(
        self, canonical_id: str, session: AsyncSession
    ) -> tuple[int, str]:
        """Resolve canonical_id → (instrument_id, asset_class)."""
        row = (
            await session.execute(
                text("SELECT id, asset_class FROM instruments WHERE canonical_id = :cid"),
                {"cid": canonical_id},
            )
        ).one_or_none()
        if row is None:
            raise InstrumentNotFound(canonical_id)
        return int(row.id), str(row.asset_class)

    async def _has_cache_gap(
        self,
        instrument_id: int,
        start: datetime,
        end: datetime,
        session: AsyncSession,
    ) -> bool:
        """Return True if bars_1m has no rows covering [start, end)."""
        row = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                      FROM bars_1m
                     WHERE instrument_id = :iid
                       AND bucket_start >= :start
                       AND bucket_start < :end
                    """
                ),
                {"iid": instrument_id, "start": start, "end": end},
            )
        ).one()
        return int(row.cnt) == 0

    async def _resolve_source(self, asset_class_key: str, session: AsyncSession) -> str:
        """Pick the highest-priority healthy source for the given asset class key."""
        row = (
            await session.execute(
                text("SELECT value_json FROM app_config WHERE namespace='charts' AND key=:k"),
                {"k": f"bar_source_priority.{asset_class_key}"},
            )
        ).one_or_none()
        priorities: list[str] = list(row.value_json) if row else _DEFAULTS.get(asset_class_key, [])

        registry = await self._get_registry()
        healthy_clients = await registry.healthy_clients()
        healthy_labels = {c.label for c in healthy_clients}

        for source in priorities:
            label = _source_to_label(source)
            if label is None:
                continue
            if label in healthy_labels:
                return source

        raise BarSourceUnavailable(
            f"no healthy sidecar for asset_class_key={asset_class_key!r}, priorities={priorities!r}"
        )

    async def _get_registry(self) -> Any:
        """Lazy-load BrokerRegistry on first call (or use injected test registry)."""
        if self._registry is None:
            from app.core.deps import get_broker_registry

            self._registry = get_broker_registry()
        return self._registry

    async def _upsert_backfill_job(
        self,
        *,
        instrument_id: int,
        source: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        session: AsyncSession,
    ) -> tuple[int, bool]:
        """INSERT INTO bar_backfill_jobs … ON CONFLICT DO NOTHING; return (id, was_new).

        was_new=True  → this worker should fetch.
        was_new=False → another worker already owns this job; caller should wait.

        The partial-unique index ``bbj_unique_pending_idx`` is the cross-worker
        chokepoint (architect CRIT #4).  We detect "was_new" via xmax=0.
        """
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO bar_backfill_jobs
                      (instrument_id, source, timeframe, range_start, range_end,
                       status, started_at)
                    VALUES
                      (:iid, :source, :tf, :start, :end, 'in_progress', NOW())
                    ON CONFLICT DO NOTHING
                    RETURNING id, (xmax = 0) AS was_new
                    """
                ),
                {
                    "iid": instrument_id,
                    "source": source,
                    "tf": timeframe,
                    "start": start,
                    "end": end,
                },
            )
        ).one_or_none()

        if row is not None:
            return int(row.id), bool(row.was_new)

        # Conflict fired (DO NOTHING) — find the existing in-progress/pending job.
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id FROM bar_backfill_jobs
                     WHERE instrument_id = :iid
                       AND source = :source
                       AND timeframe = :tf
                       AND range_start = :start
                       AND range_end = :end
                       AND status IN ('pending', 'in_progress')
                     LIMIT 1
                    """
                ),
                {
                    "iid": instrument_id,
                    "source": source,
                    "tf": timeframe,
                    "start": start,
                    "end": end,
                },
            )
        ).one_or_none()

        job_id = int(existing.id) if existing is not None else 0
        return job_id, False

    async def _primary_fetch(
        self,
        *,
        canonical_id: str,
        instrument_id: int,
        timeframe: str,
        start: datetime,
        end: datetime,
        source: str,
        job_id: int,
        session: AsyncSession,
    ) -> None:
        """Perform the actual fetch + UPSERT + mark done + pg_notify."""
        registry = await self._get_registry()
        label = _source_to_label(source)
        if label is None:
            await self._mark_job(
                job_id=job_id,
                status="failed",
                error=f"no label mapping for source={source!r}",
                session=session,
            )
            raise BarSourceUnavailable(f"no label mapping for source {source!r}")

        client = await registry.get_client(label)

        try:
            bars = await _fetch_with_chunks(
                canonical_id=canonical_id,
                tf=timeframe,
                start=start,
                end=end,
                sidecar=client,
            )
        except Exception as exc:
            await self._mark_job(job_id=job_id, status="failed", error=str(exc), session=session)
            await _emit_done(job_id, session)
            raise

        priority = _priority_for_source(source)
        rows_inserted = await _upsert_bars(
            instrument_id=instrument_id,
            source=source,
            source_priority=priority,
            bars=bars,
            session=session,
        )
        await self._mark_job(
            job_id=job_id,
            status="done",
            rows_inserted=rows_inserted,
            session=session,
        )
        await _emit_done(job_id, session)

        logger.info(
            "bar_service.primary_fetch_done",
            canonical_id=canonical_id,
            timeframe=timeframe,
            rows_inserted=rows_inserted,
            job_id=job_id,
        )

    async def _mark_job(
        self,
        *,
        job_id: int,
        status: str,
        rows_inserted: int | None = None,
        error: str | None = None,
        session: AsyncSession,
    ) -> None:
        await session.execute(
            text(
                """
                UPDATE bar_backfill_jobs
                   SET status = :status,
                       rows_inserted = :rows_inserted,
                       error_message = :error,
                       finished_at = NOW()
                 WHERE id = :job_id
                """
            ),
            {
                "status": status,
                "rows_inserted": rows_inserted,
                "error": error,
                "job_id": job_id,
            },
        )

    async def _wait_for_job(self, job_id: int) -> None:
        """Wait for another worker to finish job_id via pg_notify + 250 ms poll fallback.

        Bounded at 16 seconds total (spec CRIT #4).
        """
        from app.core.config import settings

        dsn = settings.database_url.replace("+asyncpg", "", 1)
        deadline = time.monotonic() + 16.0

        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)
        except Exception as exc:
            logger.warning("bar_service.wait_for_job.connect_failed", exc=str(exc))
            # Fall through to poll-only path.

        done_event: asyncio.Event = asyncio.Event()

        async def _notify_handler(_connection: Any, _pid: int, _channel: str, payload: str) -> None:
            try:
                if int(payload) == job_id:
                    done_event.set()
            except ValueError, TypeError:
                pass

        if conn is not None:
            try:
                await conn.add_listener("bar_backfill_done", _notify_handler)
            except Exception as exc:
                logger.warning("bar_service.wait_for_job.listen_failed", exc=str(exc))
                try:
                    await conn.close()
                except Exception:
                    pass
                conn = None

        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                if conn is not None:
                    # Wait on pg_notify event, fall back to poll every 250 ms.
                    try:
                        await asyncio.wait_for(done_event.wait(), timeout=min(0.25, remaining))
                        if done_event.is_set():
                            return
                    except TimeoutError:
                        pass
                else:
                    # Poll-only fallback.
                    await asyncio.sleep(min(0.25, remaining))

                # Poll job status directly.
                done = await _poll_job_done(job_id, conn)
                if done:
                    return

        finally:
            if conn is not None:
                try:
                    await conn.remove_listener("bar_backfill_done", _notify_handler)
                    await conn.close()
                except Exception:
                    pass

        logger.warning("bar_service.wait_for_job.timeout", job_id=job_id)

    async def _query_bars(
        self,
        *,
        instrument_id: int,
        table: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor_ts: datetime | None,
        session: AsyncSession,
    ) -> BarPage:
        """Query bars from ``table``, applying cursor pagination."""
        effective_end = cursor_ts if cursor_ts is not None else end

        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT instrument_id, bucket_start, source, source_priority,
                           open, high, low, close, volume, volume_source, trade_count
                      FROM {table}
                     WHERE instrument_id = :iid
                       AND bucket_start >= :start
                       AND bucket_start < :end
                     ORDER BY bucket_start DESC
                     LIMIT :lim
                    """
                ),
                {
                    "iid": instrument_id,
                    "start": start,
                    "end": effective_end,
                    "lim": limit + 1,
                },
            )
        ).all()

        has_more = len(rows) > limit
        page_rows = rows[:limit]

        bars = [
            Bar(
                instrument_id=r.instrument_id,
                bucket_start=r.bucket_start,
                source=r.source,
                source_priority=r.source_priority,
                open=Decimal(r.open),
                high=Decimal(r.high),
                low=Decimal(r.low),
                close=Decimal(r.close),
                volume=Decimal(r.volume) if r.volume is not None else None,
                volume_source=r.volume_source,
                trade_count=r.trade_count,
            )
            for r in page_rows
        ]

        next_cursor: str | None = None
        if has_more and bars:
            oldest = bars[-1].bucket_start
            next_cursor = _encode_cursor(oldest)

        return BarPage(bars=bars, next_cursor=next_cursor)


# ──────────────────────── Module-level helpers ───────────────────────────────


async def _fetch_with_chunks(
    canonical_id: str,
    tf: str,
    start: datetime,
    end: datetime,
    sidecar: Any,
) -> list[Any]:
    """Fetch bars in chunks, looping until truncated=False or 100-chunk cap hit."""
    from app.brokers.base import HistoricalBar

    bars: list[HistoricalBar] = []
    cursor = start
    for _chunk_idx in range(100):  # hard cap: 100 chunks per gap-fill
        resp = await sidecar.get_historical_bars(
            canonical_id=canonical_id,
            timeframe=tf,
            range_start=cursor,
            range_end=end,
            limit=1000,
        )
        bars.extend(resp.bars)
        if not resp.truncated or not resp.bars:
            return bars
        cursor = resp.bars[-1].bucket_start + _tf_to_interval(tf)
    raise BarFetchTooLarge(f"chunked fetch exceeded 100 chunks for {canonical_id}/{tf}")


async def _upsert_bars(
    *,
    instrument_id: int,
    source: str,
    source_priority: int,
    bars: list[Any],
    session: AsyncSession,
) -> int:
    """UPSERT bars into bars_1m, respecting source_priority (lower wins).

    Returns the number of rows processed.
    """
    if not bars:
        return 0

    inserted = 0
    for bar in bars:
        volume_val = str(bar.volume) if bar.volume is not None else None
        volume_src = "broker_history" if volume_val is not None else "none"

        await session.execute(
            text(
                """
                INSERT INTO bars_1m
                  (instrument_id, bucket_start, source, source_priority,
                   open, high, low, close, volume, volume_source, trade_count)
                VALUES
                  (:iid, :bs, :src, :sp, :o, :h, :l, :c, :vol, :vsrc, :tc)
                ON CONFLICT (instrument_id, bucket_start) DO UPDATE
                  SET open            = EXCLUDED.open,
                      high            = EXCLUDED.high,
                      low             = EXCLUDED.low,
                      close           = EXCLUDED.close,
                      volume          = EXCLUDED.volume,
                      volume_source   = EXCLUDED.volume_source,
                      trade_count     = EXCLUDED.trade_count,
                      source          = EXCLUDED.source,
                      source_priority = EXCLUDED.source_priority
                  WHERE EXCLUDED.source_priority < bars_1m.source_priority
                """
            ),
            {
                "iid": instrument_id,
                "bs": bar.bucket_start,
                "src": source,
                "sp": source_priority,
                "o": str(bar.open),
                "h": str(bar.high),
                "l": str(bar.low),
                "c": str(bar.close),
                "vol": volume_val,
                "vsrc": volume_src,
                "tc": bar.trade_count,
            },
        )
        inserted += 1

    return inserted


async def _emit_done(job_id: int, session: AsyncSession) -> None:
    """Execute pg_notify('bar_backfill_done', job_id) to wake waiting workers."""
    try:
        await session.execute(
            text("SELECT pg_notify('bar_backfill_done', :payload)"),
            {"payload": str(job_id)},
        )
    except Exception as exc:
        logger.warning("bar_service.emit_done.failed", job_id=job_id, exc=str(exc))


async def _poll_job_done(job_id: int, conn: Any) -> bool:
    """Poll bar_backfill_jobs for terminal status.  Returns True if done or failed."""
    if conn is None or conn.is_closed():
        return False
    try:
        row = await conn.fetchrow(
            "SELECT status FROM bar_backfill_jobs WHERE id = $1",
            job_id,
        )
        if row and row["status"] in ("done", "failed"):
            return True
    except Exception as exc:
        logger.warning("bar_service.poll_job.failed", job_id=job_id, exc=str(exc))
    return False


def _asset_class_to_priority_key(asset_class: str) -> str:
    """Map DB instrument asset_class enum value → bar_source_priority config key."""
    ac = asset_class.upper()
    if ac in ("STOCK", "ETF"):
        # TODO: HK detection deferred — treat all equities as equity_us for now.
        return "equity_us"
    if ac == "CRYPTO":
        return "crypto"
    if ac == "FOREX":
        return "fx"
    raise BarSourceUnavailable(f"no bar source priority key for asset_class={asset_class!r}")


def _source_to_label(source: str) -> str | None:
    """Map a source name → sidecar gateway label for BrokerRegistry.get_client()."""
    mapping: dict[str, str] = {
        "schwab": "schwab",
        "alpaca": "alpaca-paper",  # TODO: live-routing in a later phase
        "ibkr": "isa-live",  # First healthy IBKR — discoverer filters by health
    }
    if source == "futu":
        # TODO Phase-9-later: futu not yet wired to backend GetHistoricalBars
        logger.warning("bar_service.source_to_label.futu_not_wired", source=source)
        return None
    return mapping.get(source)


def _decode_cursor(cursor: str) -> datetime:
    """Decode a base64url cursor token → last_bucket_start datetime.

    Raises InvalidCursor if malformed or version != 1.
    """
    try:
        # Add padding if needed for standard base64 decode.
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception as exc:
        raise InvalidCursor(f"cursor decode error: {exc}") from exc

    if payload.get("v") != 1:
        raise InvalidCursor(f"cursor version {payload.get('v')!r} not supported; expected 1")

    try:
        ts_str = payload["last_bucket_start"]
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise InvalidCursor(f"invalid last_bucket_start in cursor: {exc}") from exc


def _encode_cursor(last_bucket_start: datetime) -> str:
    """Encode a datetime as a base64url cursor token."""
    ts_str = last_bucket_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps({"v": 1, "last_bucket_start": ts_str}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
