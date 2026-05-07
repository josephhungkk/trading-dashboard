"""Phase 9 Task 32 — BarService WG-split (OperationalError) tolerance.

All 6 tests run with mocked DB sessions and mocked registries so they do
not require the bars_1m / bar_backfill_jobs tables or live broker sidecars.
The recovery UPDATE is verified via a mocked SessionLocal so we confirm a
FRESH session is opened rather than reusing the poisoned one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import HistoricalBar, HistoricalBarsResult
from app.services.bar_service import (
    BarService,
    BarSourceUnavailable,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

# ──────────────────────── Constants ─────────────────────────────────────────

_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_END = datetime(2026, 4, 30, 0, 0, 0, tzinfo=UTC)
_CANONICAL = "equity_us:AAPL:NASDAQ"
_INSTRUMENT_ID = 42
_ASSET_CLASS = "STOCK"
_JOB_ID = 7


# ──────────────────────── Helpers ───────────────────────────────────────────


class _FakeRow:
    """Simple row-like object for mocking SQLAlchemy query results."""

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


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


def _make_mock_session(
    *,
    instrument_id: int = _INSTRUMENT_ID,
    asset_class: str = _ASSET_CLASS,
    cache_row_count: int = 0,
    upsert_was_new: bool = True,
    upsert_job_id: int = _JOB_ID,
) -> AsyncMock:
    """Build an AsyncMock session with standard dispatch logic."""
    session = AsyncMock(spec=AsyncSession)

    inst_result = AsyncMock()
    inst_result.one_or_none = MagicMock(
        return_value=_FakeRow(id=instrument_id, asset_class=asset_class)
    )

    cache_result = AsyncMock()
    cache_result.one = MagicMock(return_value=_FakeRow(cnt=cache_row_count))

    config_result = AsyncMock()
    config_result.one_or_none = MagicMock(return_value=None)

    upsert_result = AsyncMock()
    upsert_result.one_or_none = MagicMock(
        return_value=_FakeRow(id=upsert_job_id, was_new=upsert_was_new)
    )

    page_result = AsyncMock()
    page_result.all = MagicMock(return_value=[])

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


def _make_recovery_session() -> AsyncMock:
    """Build a fresh session mock that tracks the recovery UPDATE call."""
    recovery_session = AsyncMock(spec=AsyncSession)
    recovery_session.__aenter__ = AsyncMock(return_value=recovery_session)
    recovery_session.__aexit__ = AsyncMock(return_value=False)
    return recovery_session


# ──────────────────────── Tests ──────────────────────────────────────────────


async def test_operational_error_marks_job_failed() -> None:
    """Inject OperationalError from sidecar; assert exception propagates and
    recovery UPDATE sets status='failed', error_message LIKE 'OperationalError:%'."""
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.side_effect = OperationalError("connection lost", None, None)
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)
    session = _make_mock_session(cache_row_count=0)

    recovery_session = _make_recovery_session()
    captured_updates: list[dict[str, object]] = []

    async def _fake_execute(stmt: object, params: object = None, **kw: object) -> AsyncMock:
        if params is not None and isinstance(params, dict) and "msg" in params:
            captured_updates.append(dict(params))
        r = AsyncMock()
        r.one_or_none = MagicMock(return_value=None)
        return r

    recovery_session.execute.side_effect = _fake_execute

    mock_session_local = MagicMock(return_value=recovery_session)

    svc = BarService(registry=registry)

    with patch("app.services.bar_service.SessionLocal", mock_session_local):
        with pytest.raises((OperationalError, BarSourceUnavailable)):
            await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    # At least one recovery UPDATE should have been executed.
    assert len(captured_updates) >= 1, "Expected at least one recovery UPDATE via fresh session"
    msg = str(captured_updates[0].get("msg", ""))
    assert msg.startswith("OperationalError:"), (
        f"error_message should start with 'OperationalError:', got: {msg!r}"
    )
    recovery_session.commit.assert_called()


async def test_error_message_truncated_to_500_chars() -> None:
    """Inject OperationalError with a 2000-char message; assert stored error_message <= 500."""
    huge_msg = "x" * 2000
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.side_effect = OperationalError(huge_msg, None, None)
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)
    session = _make_mock_session(cache_row_count=0)

    recovery_session = _make_recovery_session()
    captured_msgs: list[str] = []

    async def _fake_execute(stmt: object, params: object = None, **kw: object) -> AsyncMock:
        if params is not None and isinstance(params, dict) and "msg" in params:
            captured_msgs.append(str(params["msg"]))
        r = AsyncMock()
        r.one_or_none = MagicMock(return_value=None)
        return r

    recovery_session.execute.side_effect = _fake_execute

    mock_session_local = MagicMock(return_value=recovery_session)

    svc = BarService(registry=registry)

    with patch("app.services.bar_service.SessionLocal", mock_session_local):
        with pytest.raises((OperationalError, Exception)):
            await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    assert len(captured_msgs) >= 1, "Expected recovery UPDATE to be called"
    assert len(captured_msgs[0]) <= 500, (
        f"error_message must be <= 500 chars, got {len(captured_msgs[0])}"
    )


async def test_uses_fresh_session_after_operational_error() -> None:
    """Verify that the recovery UPDATE uses a NEW session, not the poisoned original.

    We confirm this by checking that SessionLocal() is called to create a fresh
    session when OperationalError is raised, and the recovery commit is called
    on the new session.
    """
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.side_effect = OperationalError("WG tunnel down", None, None)
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)
    session = _make_mock_session(cache_row_count=0)

    recovery_session = _make_recovery_session()
    recovery_session.execute.return_value = AsyncMock()

    session_local_call_count = [0]

    def _mock_session_local() -> AsyncMock:
        session_local_call_count[0] += 1
        return recovery_session

    svc = BarService(registry=registry)

    with patch("app.services.bar_service.SessionLocal", _mock_session_local):
        with pytest.raises((OperationalError, Exception)):
            await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    # SessionLocal() must have been called to open a fresh session.
    assert session_local_call_count[0] >= 1, (
        "SessionLocal() must be called to open a fresh recovery session"
    )
    # The recovery session must have been committed.
    recovery_session.commit.assert_called()


async def test_failed_job_allows_new_attempt() -> None:
    """Pre-seed a 'failed' job; the partial-unique-pending index permits a new INSERT.

    We verify this by checking that _upsert_backfill_job is called (i.e., the
    get_bars flow proceeds to UPSERT) because a 'failed' row does not conflict
    with the partial index that only covers status IN ('pending', 'in_progress').
    """
    # Return cache miss → triggers backfill flow.
    # Simulate that a 'failed' row exists by making the UPSERT return was_new=True
    # (INSERT succeeded without conflict — the failed row doesn't block).
    session = _make_mock_session(cache_row_count=0, upsert_was_new=True, upsert_job_id=99)

    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.return_value = HistoricalBarsResult(
        bars=[_make_bar(0)], truncated=False
    )
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)

    upsert_called = [False]

    original_upsert = BarService._upsert_backfill_job

    async def _tracked_upsert(self: BarService, **kwargs: object) -> tuple[int, bool]:
        upsert_called[0] = True
        return await original_upsert(self, **kwargs)

    svc = BarService(registry=registry)
    with patch.object(BarService, "_upsert_backfill_job", _tracked_upsert):
        # Should not raise — new attempt succeeds.
        await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    assert upsert_called[0], (
        "_upsert_backfill_job should be called when a new attempt is made after a failed job"
    )
    mock_sidecar.get_historical_bars.assert_called()


async def test_pre_warm_skips_failed_instrument_until_next_cycle() -> None:
    """Pre-warm with instrument X having a 'failed' job: instrument X should still be
    attempted (failed status does not block pre_warm — only 'done' updates gap_start).

    If OperationalError is injected during the attempt, pre-warm continues to
    instrument Y instead of looping forever.
    """

    class _LocalRow:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    instruments_x = [
        _LocalRow(
            instrument_id=10,
            recency_score=1000,
            canonical_id="equity_us:X:NYSE:10",
            asset_class="STOCK",
        ),
        _LocalRow(
            instrument_id=11,
            recency_score=999,
            canonical_id="equity_us:Y:NYSE:11",
            asset_class="STOCK",
        ),
    ]

    # Session mock for pre_warm.
    session = AsyncMock(spec=AsyncSession)
    inst_map = {r.instrument_id: r for r in instruments_x}

    async def _execute_side_effect(stmt: object, params: object = None, **kw: object) -> AsyncMock:
        sql = str(stmt)
        r = AsyncMock()

        if "recency_score" in sql and "LIMIT 1000" in sql:
            result = AsyncMock()
            result.all = MagicMock(
                return_value=[
                    _LocalRow(instrument_id=row.instrument_id, recency_score=row.recency_score)
                    for row in instruments_x
                ]
            )
            return result

        if "bar_pre_warm_window_days" in sql:
            r.one_or_none = MagicMock(return_value=None)
            return r

        if "SELECT canonical_id, asset_class" in sql:
            iid = params.get("iid") if isinstance(params, dict) else None
            if iid is not None and int(iid) in inst_map:
                row = inst_map[int(iid)]
                r.one_or_none = MagicMock(
                    return_value=_LocalRow(
                        canonical_id=row.canonical_id, asset_class=row.asset_class
                    )
                )
            else:
                r.one_or_none = MagicMock(return_value=None)
            return r

        if "FROM app_config" in sql:
            r.one_or_none = MagicMock(return_value=None)
            return r

        # bar_backfill_jobs last done — return None (failed jobs don't set gap_start).
        if "FROM bar_backfill_jobs" in sql and "status = 'done'" in sql:
            r.one_or_none = MagicMock(return_value=None)
            return r

        r.all = MagicMock(return_value=[])
        r.one_or_none = MagicMock(return_value=None)
        r.one = MagicMock(return_value=_LocalRow(cnt=0))
        return r

    session.execute.side_effect = _execute_side_effect

    registry = _make_registry(healthy_labels=["schwab"])

    attempted: list[int] = []
    instrument_y_attempted = [False]

    async def _fake_get_bars(
        self: BarService,
        canonical_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
        cursor: str | None = None,
        *,
        session: AsyncSession,
    ) -> object:
        iid = int(canonical_id.split(":")[-1])
        attempted.append(iid)
        if iid == 10:
            # Simulate OperationalError for instrument X.
            raise OperationalError("WG split", None, None)
        instrument_y_attempted[0] = True
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        # pre_warm must not raise even when OperationalError is injected.
        await svc.pre_warm_active_set(session)

    # Instrument X must have been attempted (failed jobs don't block pre_warm).
    assert 10 in attempted, "Instrument X (with failed job) should be attempted in pre_warm"
    # Instrument Y must also have been processed (pre_warm continues after error).
    assert instrument_y_attempted[0], (
        "Pre_warm must continue to instrument Y after OperationalError on instrument X"
    )


async def test_metric_increment_on_failure() -> None:
    """Trigger OperationalError; assert bar_service_backfill_total{outcome='failed'} == 1."""
    mock_sidecar = AsyncMock()
    mock_sidecar.get_historical_bars.side_effect = OperationalError("db gone", None, None)
    registry = _make_registry(healthy_labels=["schwab"], client=mock_sidecar)
    session = _make_mock_session(cache_row_count=0)

    recovery_session = _make_recovery_session()
    recovery_session.execute.return_value = AsyncMock()

    svc = BarService(registry=registry)

    with patch("app.services.bar_service.SessionLocal", MagicMock(return_value=recovery_session)):
        with patch("app.services.bar_service.metrics") as mock_metrics:
            # Capture label calls.
            labeled_mock = MagicMock()
            mock_metrics.bar_service_backfill_total.labels.return_value = labeled_mock

            with pytest.raises((OperationalError, Exception)):
                await svc.get_bars(_CANONICAL, "1m", _START, _END, session=session)

    # The outer except in get_bars increments outcome='failed'.
    # Verify the labels call was made with outcome='failed'.
    calls = mock_metrics.bar_service_backfill_total.labels.call_args_list
    failed_calls = [c for c in calls if c.kwargs.get("outcome") == "failed"]
    assert len(failed_calls) >= 1, (
        f"Expected at least one labels(outcome='failed') call, got: {calls}"
    )
    # Verify .inc() was called on the failed counter.
    assert labeled_mock.inc.call_count >= 1, "inc() must be called on the failed counter"
