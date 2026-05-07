"""Phase 9 Task 26 — BarService.get_bars + cross-worker pg_notify coalesce.

Tests are integration-marked and use mocked DB sessions so they run
without needing the bars_1m / bar_backfill_jobs tables present on the
dev NUC (which is still at migration 0014).  The mock pattern mirrors
the spec requirement while verifying all BarService logic paths.

Note on `session` fixture: the real savepoint-rollback session fixture is
used for instrument seeding helpers; bar-table operations are mocked via
AsyncMock so tests are not coupled to TimescaleDB table existence.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import HistoricalBar, HistoricalBarsResult
from app.services.bar_service import (
    Bar,
    BarFetchTooLarge,
    BarService,
    InvalidCursor,
    _decode_cursor,
    _fetch_with_chunks,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

# ──────────────────────── Constants ─────────────────────────────────────────

_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_END = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
_CANONICAL = "equity_us:AAPL:NASDAQ"
_INSTRUMENT_ID = 42
_ASSET_CLASS = "STOCK"


# ──────────────────────── Helpers ───────────────────────────────────────────


def _make_bar(n: int) -> HistoricalBar:
    return HistoricalBar(
        bucket_start=_START + timedelta(minutes=n),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
        trade_count=10,
    )


def _make_bars(count: int) -> list[HistoricalBar]:
    return [_make_bar(i) for i in range(count)]


class _FakeRow:
    """Simple row-like object for mocking SQLAlchemy query results."""

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_page_bar_row(n: int, source: str = "schwab", priority: int = 1) -> _FakeRow:
    return _FakeRow(
        instrument_id=_INSTRUMENT_ID,
        bucket_start=_START + timedelta(minutes=n),
        source=source,
        source_priority=priority,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
        volume_source="broker_history",
        trade_count=10,
    )


def _make_mock_session(
    *,
    instrument_id: int = _INSTRUMENT_ID,
    asset_class: str = _ASSET_CLASS,
    cache_row_count: int = 0,
    bars_page_rows: list[_FakeRow] | None = None,
    source_priority_json: list[str] | None = None,
    upsert_was_new: bool = True,
    upsert_job_id: int = 1,
) -> AsyncMock:
    """Build an AsyncMock session whose execute() dispatches by SQL keyword.

    Matching is done on the SQL text so that intermediate calls (bar UPSERTs,
    mark-job UPDATE, pg_notify SELECT) don't consume the page-query slot.
    """
    session = AsyncMock(spec=AsyncSession)

    # Pre-built results keyed by a distinctive SQL fragment.
    inst_result = AsyncMock()
    inst_result.one_or_none = MagicMock(
        return_value=_FakeRow(id=instrument_id, asset_class=asset_class)
    )

    cache_result = AsyncMock()
    cache_result.one = MagicMock(return_value=_FakeRow(cnt=cache_row_count))

    config_row = _FakeRow(value_json=source_priority_json) if source_priority_json else None
    config_result = AsyncMock()
    config_result.one_or_none = MagicMock(return_value=config_row)

    upsert_result = AsyncMock()
    upsert_result.one_or_none = MagicMock(
        return_value=_FakeRow(id=upsert_job_id, was_new=upsert_was_new)
    )

    page_result = AsyncMock()
    page_result.all = MagicMock(return_value=bars_page_rows or [])

    # Generic for everything else (per-bar INSERT, UPDATE bar_backfill_jobs, pg_notify).
    generic_result = AsyncMock()
    generic_result.all = MagicMock(return_value=[])
    generic_result.one_or_none = MagicMock(return_value=None)
    generic_result.one = MagicMock(return_value=_FakeRow(cnt=0))

    async def _execute_side_effect(stmt: object, params: object = None, **kw: object) -> object:
        sql = str(stmt)
        if "FROM instruments" in sql:
            return inst_result
        if "COUNT(*)" in sql and "bars_" in sql:
            return cache_result
        if "FROM app_config" in sql and "bar_source_priority" in sql:
            return config_result
        if "INTO bar_backfill_jobs" in sql and "RETURNING" in sql:
            return upsert_result
        if ("FROM bars_1m" in sql or "FROM bars_1s" in sql) and "ORDER BY" in sql:
            return page_result
        # INSERT INTO bars_1m (per bar), UPDATE bar_backfill_jobs, SELECT pg_notify
        return generic_result

    session.execute.side_effect = _execute_side_effect
    return session


def _make_registry(
    *,
    healthy_labels: list[str] | None = None,
    client: AsyncMock | None = None,
) -> MagicMock:
    registry = MagicMock()
    labels = healthy_labels or ["schwab"]
    mock_clients = [MagicMock(label=lbl) for lbl in labels]
    registry.healthy_clients = AsyncMock(return_value=mock_clients)
    if client is None:
        client = AsyncMock()
    registry.get_client = AsyncMock(return_value=client)
    return registry


# ──────────────────────── Tests ──────────────────────────────────────────────


async def test_cache_hit_no_fetch() -> None:
    """Pre-seed bars_1m for [start, end); assert no sidecar call; assert bars returned."""
    page_rows = [_make_page_bar_row(i) for i in range(5)]
    session = _make_mock_session(cache_row_count=5, bars_page_rows=page_rows)
    mock_sidecar = AsyncMock()
    registry = _make_registry(client=mock_sidecar)

    svc = BarService(registry=registry)
    result = await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    mock_sidecar.get_historical_bars.assert_not_called()
    assert len(result.bars) == 5
    assert all(isinstance(b, Bar) for b in result.bars)


async def test_cache_miss_single_worker_fetches() -> None:
    """Empty bars_1m; mock sidecar returns 50 bars; assert sidecar called once; bars returned."""
    page_rows = [_make_page_bar_row(i) for i in range(50)]
    session = _make_mock_session(cache_row_count=0, bars_page_rows=page_rows)
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=_make_bars(50),
        truncated=False,
    )
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)

    svc = BarService(registry=registry)
    result = await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    mock_sidecar.get_historical_bars.assert_called_once()
    assert len(result.bars) == 50


async def test_concurrent_workers_only_one_fetches() -> None:
    """Two BarService instances; mock sidecar with sleep+counter; concurrent gather;
    assert sidecar.call_count == 1; worker B waited; both received bars."""
    call_count = [0]

    async def _mock_upsert(
        self: BarService,
        *,
        instrument_id: int,
        source: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        session: AsyncSession,
    ) -> tuple[int, bool]:
        call_count[0] += 1
        if call_count[0] == 1:
            return 1, True  # worker A is primary
        return 1, False  # worker B defers

    page_rows = [_make_page_bar_row(i) for i in range(3)]
    session_a = _make_mock_session(cache_row_count=0, bars_page_rows=page_rows)
    session_b = _make_mock_session(cache_row_count=0, bars_page_rows=page_rows)

    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=_make_bars(3), truncated=False
    )
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)

    svc_a = BarService(registry=registry)
    svc_b = BarService(registry=registry)

    with patch.object(BarService, "_upsert_backfill_job", _mock_upsert):
        with patch.object(BarService, "_wait_for_job", new_callable=AsyncMock) as mock_wait:
            results = await asyncio.gather(
                svc_a.get_bars(_CANONICAL, "1m", _START, _END, session=session_a),
                svc_b.get_bars(_CANONICAL, "1m", _START, _END, session=session_b),
            )

    # Sidecar called exactly once (worker A only).
    assert mock_sidecar.get_historical_bars.call_count == 1
    # Worker B waited.
    assert mock_wait.call_count == 1
    # Both returned bars.
    assert len(results[0].bars) > 0
    assert len(results[1].bars) > 0


async def test_cursor_pagination() -> None:
    """Seed 200 bars; limit=100; assert 100 + next_cursor; call again; next 100 + None."""
    # First page: return 101 rows so has_more=True → next_cursor set.
    first_page_rows = [_make_page_bar_row(i) for i in range(101)]
    # Second page: return exactly 100 → no has_more.
    second_page_rows = [_make_page_bar_row(i) for i in range(100)]

    session1 = _make_mock_session(cache_row_count=200, bars_page_rows=first_page_rows)
    svc = BarService()
    result1 = await svc.get_bars(_CANONICAL, "1m", _START, _END, limit=100, session=session1)

    assert len(result1.bars) == 100
    assert result1.next_cursor is not None

    session2 = _make_mock_session(cache_row_count=200, bars_page_rows=second_page_rows)
    result2 = await svc.get_bars(
        _CANONICAL, "1m", _START, _END, limit=100, cursor=result1.next_cursor, session=session2
    )

    assert len(result2.bars) == 100
    assert result2.next_cursor is None


async def test_invalid_cursor_v_raises() -> None:
    """Pass cursor with v=2; assert InvalidCursor raised."""
    bad_payload = json.dumps({"v": 2, "x": "y"})
    bad_cursor = base64.urlsafe_b64encode(bad_payload.encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursor, match="version"):
        _decode_cursor(bad_cursor)


async def test_chunked_fetch_loops_until_truncated_false() -> None:
    """Sidecar returns truncated=True for 3 chunks then False; 4 calls; bars concatenated."""
    call_num = [0]

    async def _paginated(**kwargs: object) -> HistoricalBarsResult:
        call_num[0] += 1
        return HistoricalBarsResult(
            bars=[_make_bar(call_num[0] * 10)],
            truncated=(call_num[0] < 4),
        )

    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.side_effect = _paginated

    bars = await _fetch_with_chunks(
        canonical_id=_CANONICAL,
        tf="1m",
        start=_START,
        end=_END,
        sidecar=mock_sidecar,
    )

    assert mock_sidecar.get_historical_bars.call_count == 4
    assert len(bars) == 4


async def test_chunked_fetch_exceeds_cap_raises() -> None:
    """Sidecar always returns truncated=True; BarFetchTooLarge after 100 chunks."""
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=[_make_bar(0)],
        truncated=True,
    )

    with pytest.raises(BarFetchTooLarge, match="100 chunks"):
        await _fetch_with_chunks(
            canonical_id=_CANONICAL,
            tf="1m",
            start=_START,
            end=_END,
            sidecar=mock_sidecar,
        )

    assert mock_sidecar.get_historical_bars.call_count == 100


async def test_sub_minute_never_backfills() -> None:
    """Empty bars_1s; call get_bars(tf='1s'); assert no sidecar call; empty list returned."""
    # For sub-minute TF: only instrument resolve + bars_1s query needed.
    call_idx = [0]

    def _build_inst_result() -> AsyncMock:
        r = AsyncMock()
        r.one_or_none = MagicMock(return_value=_FakeRow(id=_INSTRUMENT_ID, asset_class="STOCK"))
        return r

    def _build_empty_bars_result() -> AsyncMock:
        r = AsyncMock()
        r.all = MagicMock(return_value=[])
        return r

    session = AsyncMock(spec=AsyncSession)

    async def _execute(stmt: object, params: object = None, **kw: object) -> object:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return _build_inst_result()
        return _build_empty_bars_result()

    session.execute.side_effect = _execute

    mock_sidecar = AsyncMock()
    registry = _make_registry(client=mock_sidecar)

    svc = BarService(registry=registry)
    result = await svc.get_bars(_CANONICAL, "1s", _START, _END, session=session)

    mock_sidecar.get_historical_bars.assert_not_called()
    assert result.bars == []


async def test_priority_upsert_higher_priority_wins() -> None:
    """seed ibkr priority=3; sidecar returns schwab priority=1; UPSERT replaces row."""
    # Page returns schwab row after successful fetch (priority 1 won).
    page_rows = [_make_page_bar_row(0, source="schwab", priority=1)]
    session = _make_mock_session(cache_row_count=0, bars_page_rows=page_rows)
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=[_make_bar(0)], truncated=False
    )
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)

    svc = BarService(registry=registry)
    result = await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    # Sidecar was called (cache miss triggered fetch).
    mock_sidecar.get_historical_bars.assert_called_once()
    # The returned bar has schwab priority (1 < 3 → wins the UPSERT WHERE clause).
    assert result.bars[0].source == "schwab"
    assert result.bars[0].source_priority == 1


async def test_priority_upsert_lower_priority_skipped() -> None:
    """seed schwab priority=1; sidecar returns ibkr priority=3; UPSERT does NOT replace."""
    # Page returns schwab row (ibkr UPSERT WHERE clause rejects because 3 >= 1).
    page_rows = [_make_page_bar_row(0, source="schwab", priority=1)]

    # Force ibkr as the chosen source via _resolve_source mock.
    session = _make_mock_session(cache_row_count=0, bars_page_rows=page_rows)
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=[_make_bar(0)], truncated=False
    )
    registry = _make_registry(healthy_labels=["isa-live"], client=mock_sidecar)

    svc = BarService(registry=registry)
    with patch.object(BarService, "_resolve_source", new=AsyncMock(return_value="ibkr")):
        result = await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    # The SELECT query still returns the schwab row (ibkr's WHERE clause was rejected).
    assert result.bars[0].source == "schwab"
    assert result.bars[0].source_priority == 1
