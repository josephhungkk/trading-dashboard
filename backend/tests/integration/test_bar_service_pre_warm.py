"""Phase 9 Task 27 — BarService.pre_warm_active_set + cron schedule.

All 9 tests run with mocked DB sessions and mocked registries so they do
not require the bars_1m / bar_backfill_jobs tables or live broker sidecars.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bar_service import BarService
from app.services.broker_registry_factory import MissingBrokerSecrets

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]

# ──────────────────────── Constants ─────────────────────────────────────────

_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
_30D_AGO = _NOW - timedelta(days=30)


# ──────────────────────── Helpers ───────────────────────────────────────────


class _FakeRow:
    """Simple row-like object for mocking SQLAlchemy query results."""

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_registry(
    *,
    healthy_labels: list[str] | None = None,
) -> MagicMock:
    registry = MagicMock()
    labels = healthy_labels if healthy_labels is not None else ["schwab"]
    mock_clients = [MagicMock(label=lbl) for lbl in labels]
    registry.healthy_clients = AsyncMock(return_value=mock_clients)
    registry.get_client = AsyncMock(return_value=AsyncMock())
    return registry


def _make_instruments(
    instrument_ids: list[int],
    asset_class: str = "STOCK",
    canonical_prefix: str = "equity_us:AAPL:NASDAQ",
) -> list[_FakeRow]:
    return [
        _FakeRow(
            instrument_id=iid,
            recency_score=1000 - i,
            canonical_id=f"{canonical_prefix}:{iid}",
            asset_class=asset_class,
        )
        for i, iid in enumerate(instrument_ids)
    ]


def _make_pre_warm_session(
    instruments: list[_FakeRow],
    *,
    last_done_range_end: datetime | None = None,
    window_days: int | None = None,
    source_priority: list[str] | None = None,
) -> AsyncMock:
    """Build a session mock that answers all queries needed by pre_warm_active_set."""
    session = AsyncMock(spec=AsyncSession)

    # active_set query returns the instrument list.
    active_set_result = AsyncMock()
    active_set_result.all = MagicMock(
        return_value=[
            _FakeRow(instrument_id=r.instrument_id, recency_score=r.recency_score)
            for r in instruments
        ]
    )

    # Build a lookup map by instrument_id.
    inst_map = {r.instrument_id: r for r in instruments}

    # app_config window_days row.
    window_result = AsyncMock()
    window_result.one_or_none = MagicMock(
        return_value=_FakeRow(value=str(window_days)) if window_days is not None else None
    )

    # app_config source_priority row.
    src_priority_result = AsyncMock()
    src_priority_result.one_or_none = MagicMock(
        return_value=_FakeRow(value_json=source_priority) if source_priority else None
    )

    # bar_backfill_jobs last done.
    jobs_result = AsyncMock()
    jobs_result.one_or_none = MagicMock(
        return_value=(
            _FakeRow(range_end=last_done_range_end) if last_done_range_end is not None else None
        )
    )

    # Generic fallback (for anything else).
    generic_result = AsyncMock()
    generic_result.all = MagicMock(return_value=[])
    generic_result.one_or_none = MagicMock(return_value=None)
    generic_result.one = MagicMock(return_value=_FakeRow(has_data=False))

    async def _execute_side_effect(stmt: object, params: object = None, **kw: object) -> object:
        sql = str(stmt)

        # active_set query (has recency_score in SELECT and LIMIT 1000)
        if "recency_score" in sql and "LIMIT 1000" in sql:
            return active_set_result

        # window_days config
        if "FROM app_config" in sql and "bar_pre_warm_window_days" in sql:
            return window_result

        # HIGH-7: batch instrument lookup (WHERE id = ANY(:ids)) — returns all rows.
        if "ANY(:ids)" in sql and "canonical_id" in sql:
            result = AsyncMock()
            ids = params.get("ids") if isinstance(params, dict) else None
            if ids is not None:
                rows = [
                    _FakeRow(
                        id=iid,
                        canonical_id=inst_map[iid].canonical_id,
                        asset_class=inst_map[iid].asset_class,
                    )
                    for iid in [int(x) for x in ids]
                    if iid in inst_map
                ]
            else:
                rows = []
            result.all = MagicMock(return_value=rows)
            return result

        # source_priority config — the key 'bar_source_priority.*' is passed as :k param,
        # not embedded in the SQL string. Detect by presence of ":k" param.
        if "FROM app_config" in sql and "bar_pre_warm_window_days" not in sql:
            param_k = params.get("k") if isinstance(params, dict) else None
            if param_k is not None and str(param_k).startswith("bar_source_priority"):
                return src_priority_result

        # bar_backfill_jobs last done
        if "FROM bar_backfill_jobs" in sql and "status = 'done'" in sql:
            return jobs_result

        return generic_result

    session.execute.side_effect = _execute_side_effect
    return session


# ──────────────────────── Tests ──────────────────────────────────────────────


async def test_pre_warm_runs_for_each_instrument_and_timeframe() -> None:
    """Seed 3 instruments; mock get_bars to record calls; assert 9 (3x3) tuples called."""
    instruments = _make_instruments([1, 2, 3])
    session = _make_pre_warm_session(instruments, last_done_range_end=_30D_AGO)
    registry = _make_registry(healthy_labels=["schwab"])

    called: list[tuple[str, str]] = []

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
        called.append((canonical_id, timeframe))
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await svc.pre_warm_active_set(session)

    assert len(called) == 9
    timeframes_called = [tf for _, tf in called]
    assert timeframes_called.count("1m") == 3
    assert timeframes_called.count("1h") == 3
    assert timeframes_called.count("1d") == 3


async def test_pre_warm_skips_when_no_healthy_non_ibkr_source() -> None:
    """Instrument with asset_class STOCK; only ibkr healthy; assert instrument skipped."""
    instruments = _make_instruments([10], asset_class="STOCK")
    session = _make_pre_warm_session(instruments)
    # Only IBKR is healthy — no Schwab/Alpaca.
    registry = _make_registry(healthy_labels=["isa-live"])

    called: list[str] = []

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
        called.append(canonical_id)
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        await svc.pre_warm_active_set(session)

    assert len(called) == 0, "IBKR-only instruments should be skipped during pre_warm"


async def test_pre_warm_uses_jittered_ibkr_label_for_hk() -> None:
    """HK instrument; futu wired and healthy; get_bars called for all 3 timeframes."""
    iid = 42
    instruments = [
        _FakeRow(
            instrument_id=iid,
            recency_score=1000,
            canonical_id=f"equity_hk:0700.HK:{iid}",
            asset_class="STOCK",
        )
    ]
    session = _make_pre_warm_session(
        instruments,
        last_done_range_end=_30D_AGO,
        source_priority=["futu", "ibkr"],
    )

    with patch("app.services.bar_service._source_to_label") as mock_label:

        def _label_side_effect(source: str) -> str | None:
            if source == "futu":
                return "futu-sidecar"
            if source == "ibkr":
                return f"ibkr-{(iid % 4) + 1:03d}"
            return None

        mock_label.side_effect = _label_side_effect
        registry = _make_registry(healthy_labels=["futu-sidecar"])
        called: list[tuple[str, str]] = []

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
            called.append((canonical_id, timeframe))
            return MagicMock(bars=[], next_cursor=None)

        svc = BarService(registry=registry)
        with patch.object(BarService, "get_bars", _fake_get_bars):
            with patch("app.services.bar_service.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await svc.pre_warm_active_set(session)

    # futu is healthy → all 3 timeframes processed (not skipped).
    assert len(called) == 3
    assert all(tf in ("1m", "1h", "1d") for _, tf in called)


async def test_pre_warm_uses_last_done_range_end() -> None:
    """bar_backfill_jobs row with status=done range_end=2026-04-01; assert gap_start=that date."""
    last_done = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
    instruments = _make_instruments([5])
    session = _make_pre_warm_session(instruments, last_done_range_end=last_done)
    registry = _make_registry(healthy_labels=["schwab"])

    captured_starts: list[datetime] = []

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
        captured_starts.append(start)
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await svc.pre_warm_active_set(session)

    assert len(captured_starts) > 0
    for s in captured_starts:
        assert s == last_done, f"expected {last_done}, got {s}"


async def test_pre_warm_uses_30d_default_when_no_last_done() -> None:
    """Instrument with no bar_backfill_jobs row; assert gap_start is approx now-30d."""
    instruments = _make_instruments([7])
    session = _make_pre_warm_session(instruments)  # no last_done_range_end
    registry = _make_registry(healthy_labels=["schwab"])

    captured_starts: list[datetime] = []

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
        captured_starts.append(start)
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await svc.pre_warm_active_set(session)

    assert len(captured_starts) > 0
    expected = _NOW - timedelta(days=30)
    for s in captured_starts:
        diff = abs((s - expected).total_seconds())
        assert diff < 1.0, f"gap_start {s} is not within 1s of {expected}"


async def test_pre_warm_yields_between_instruments() -> None:
    """Seed 5 instruments; mock asyncio.sleep; assert sleep(0) called at least 5 times."""
    instruments = _make_instruments([1, 2, 3, 4, 5])
    session = _make_pre_warm_session(instruments, last_done_range_end=_30D_AGO)
    registry = _make_registry(healthy_labels=["schwab"])

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def _fake_get_bars(
        self: BarService,
        *_: object,
        **__: object,
    ) -> object:
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch("app.services.bar_service.asyncio.sleep", side_effect=_fake_sleep):
        with patch.object(BarService, "get_bars", _fake_get_bars):
            with patch("app.services.bar_service.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await svc.pre_warm_active_set(session)

    zero_sleeps = [d for d in sleep_calls if d == 0]
    assert len(zero_sleeps) >= 5, (
        f"Expected >= 5 sleep(0) calls (one per instrument), got {len(zero_sleeps)}"
    )


def _make_lifespan_mocks(
    mock_redis_cls: MagicMock,
    mock_bridge_cls: MagicMock,
    mock_cache_cls: MagicMock,
    mock_cbs: MagicMock,
    mock_bar_cls: MagicMock,
) -> None:
    """Configure all mock classes needed to run the FastAPI lifespan without real I/O."""
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()
    # `redis.pubsub()` is a sync method returning an instance that IS an
    # async context manager. AsyncMock would return a coroutine and break
    # `async with self._redis.pubsub() as p:` in OrderCapabilityService.run_listener.
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()

    async def _empty_listen():
        if False:  # pragma: no cover
            yield None

    mock_pubsub.listen = MagicMock(side_effect=_empty_listen)
    mock_pubsub.__aenter__ = AsyncMock(return_value=mock_pubsub)
    mock_pubsub.__aexit__ = AsyncMock(return_value=None)
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    mock_redis_cls.from_url.return_value = mock_redis

    mock_bridge = MagicMock()
    mock_bridge.stop = MagicMock()
    mock_bridge.run = AsyncMock(return_value=None)
    mock_bridge_cls.return_value = mock_bridge

    # ConfigCache instances need run_listener() to be awaitable (used in create_task).
    mock_cache_inst = AsyncMock()
    mock_cache_inst.run_listener = AsyncMock(return_value=None)
    mock_cache_cls.return_value = mock_cache_inst

    mock_cb_server = AsyncMock()
    mock_cb_server.stop = AsyncMock()
    mock_cbs.return_value = mock_cb_server

    mock_bar_svc = AsyncMock()
    mock_bar_svc.start = AsyncMock()
    mock_bar_svc.stop = AsyncMock()
    mock_bar_cls.return_value = mock_bar_svc


async def test_lifespan_starts_scheduler() -> None:
    """Invoke lifespan directly; assert scheduler exists, is running, has 3 cron jobs."""
    from app.main import app as fastapi_app
    from app.main import lifespan

    with (
        patch("app.main.Redis") as mock_redis_cls,
        patch("app.main.PostgresListenBridge") as mock_bridge_cls,
        patch("app.main.ConfigCache") as mock_cache_cls,
        patch("app.main.ConfigService"),
        patch("app.main.get_fernet"),
        patch("app.main.set_config_service"),
        patch("app.main.start_backend_callback_server") as mock_cbs,
        patch(
            "app.main.build_broker_registry",
            side_effect=MissingBrokerSecrets("no broker"),
        ),
        patch("app.main.seed_instruments_from_positions", return_value=0),
        patch("app.main.BarService") as mock_bar_cls,
        patch("app.main._run_pre_warm", new_callable=AsyncMock),
    ):
        _make_lifespan_mocks(
            mock_redis_cls, mock_bridge_cls, mock_cache_cls, mock_cbs, mock_bar_cls
        )

        async with lifespan(fastapi_app):
            scheduler = fastapi_app.state.scheduler
            assert scheduler is not None
            assert scheduler.running is True
            jobs = scheduler.get_jobs()
            assert len(jobs) == 3, f"Expected 3 cron jobs, got {len(jobs)}"


async def test_lifespan_runs_initial_pre_warm() -> None:
    """Invoke lifespan directly; assert _run_pre_warm is called during startup."""
    from app.main import app as fastapi_app
    from app.main import lifespan

    pre_warm_called = asyncio.Event()

    async def _counting_pre_warm() -> None:
        pre_warm_called.set()

    with (
        patch("app.main.Redis") as mock_redis_cls,
        patch("app.main.PostgresListenBridge") as mock_bridge_cls,
        patch("app.main.ConfigCache") as mock_cache_cls,
        patch("app.main.ConfigService"),
        patch("app.main.get_fernet"),
        patch("app.main.set_config_service"),
        patch("app.main.start_backend_callback_server") as mock_cbs,
        patch(
            "app.main.build_broker_registry",
            side_effect=MissingBrokerSecrets("no broker"),
        ),
        patch("app.main.seed_instruments_from_positions", return_value=0),
        patch("app.main.BarService") as mock_bar_cls,
        patch("app.main._run_pre_warm", new_callable=AsyncMock) as mock_pre_warm,
    ):
        mock_pre_warm.side_effect = _counting_pre_warm
        _make_lifespan_mocks(
            mock_redis_cls, mock_bridge_cls, mock_cache_cls, mock_cbs, mock_bar_cls
        )

        async with lifespan(fastapi_app):
            try:
                await asyncio.wait_for(pre_warm_called.wait(), timeout=0.5)
            except TimeoutError:
                pass

    assert pre_warm_called.is_set(), "_run_pre_warm should have been called during startup"


async def test_pre_warm_handles_get_bars_error_gracefully() -> None:
    """Mock get_bars to raise on instrument 2/1m; assert pre_warm continues + 1h/1d succeed."""
    instruments = _make_instruments([1, 2, 3])
    session = _make_pre_warm_session(instruments, last_done_range_end=_30D_AGO)
    registry = _make_registry(healthy_labels=["schwab"])

    successful: list[tuple[int, str]] = []

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
        # canonical_id ends with ":{instrument_id}".
        iid = int(canonical_id.split(":")[-1])
        if iid == 2 and timeframe == "1m":
            raise RuntimeError("simulated sidecar error for instrument 2 / 1m")
        successful.append((iid, timeframe))
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Must not raise.
            await svc.pre_warm_active_set(session)

    iid1_tfs = [tf for iid, tf in successful if iid == 1]
    iid3_tfs = [tf for iid, tf in successful if iid == 3]
    iid2_tfs = [tf for iid, tf in successful if iid == 2]

    assert set(iid1_tfs) == {"1m", "1h", "1d"}, "Instrument 1 should be fully processed"
    assert set(iid3_tfs) == {"1m", "1h", "1d"}, "Instrument 3 should be fully processed"
    # Instrument 2: 1m raised, but 1h and 1d should have succeeded.
    assert "1h" in iid2_tfs and "1d" in iid2_tfs, (
        "Instrument 2: 1h and 1d should succeed even though 1m raised"
    )


# ──────────────────── Tests for reviewer-fix batch A (HIGH-7) ────────────────


async def test_high7_instruments_queried_with_any_ids_batch() -> None:
    """HIGH-7: assert instruments table is queried with ANY(:ids) (1 query for N instruments).

    We intercept session.execute() and count how many times a query matching
    'ANY(:ids)' is executed for the instruments table. For N instruments, it
    must be exactly 1.
    """
    n = 5
    instruments = _make_instruments(list(range(1, n + 1)))
    session = _make_pre_warm_session(instruments, last_done_range_end=_30D_AGO)
    registry = _make_registry(healthy_labels=["schwab"])

    any_ids_call_count = [0]
    original_side_effect = session.execute.side_effect

    async def _tracking_execute(stmt: object, params: object = None, **kw: object) -> object:
        sql = str(stmt)
        if "ANY(:ids)" in sql and "canonical_id" in sql:
            any_ids_call_count[0] += 1
        return await original_side_effect(stmt, params, **kw)

    session.execute.side_effect = _tracking_execute

    async def _fake_get_bars(self: BarService, *_: object, **__: object) -> object:
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await svc.pre_warm_active_set(session)

    assert any_ids_call_count[0] == 1, (
        f"instruments table must be queried exactly once using ANY(:ids), "
        f"got {any_ids_call_count[0]} calls"
    )


async def test_high7_healthy_clients_called_once_per_cycle() -> None:
    """HIGH-7: registry.healthy_clients() must be called exactly once per pre_warm cycle,
    not once per instrument.
    """
    n = 4
    instruments = _make_instruments(list(range(1, n + 1)))
    session = _make_pre_warm_session(instruments, last_done_range_end=_30D_AGO)
    registry = _make_registry(healthy_labels=["schwab"])

    async def _fake_get_bars(self: BarService, *_: object, **__: object) -> object:
        return MagicMock(bars=[], next_cursor=None)

    svc = BarService(registry=registry)
    with patch.object(BarService, "get_bars", _fake_get_bars):
        with patch("app.services.bar_service.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await svc.pre_warm_active_set(session)

    # healthy_clients() should have been called once (by pre_warm) + once (by _get_registry
    # in get_bars if get_bars is patched then healthy_clients is NOT called inside the loop).
    # Since get_bars is patched away, the only call is from pre_warm_active_set itself.
    assert registry.healthy_clients.call_count == 1, (
        f"healthy_clients() must be called once per cycle, "
        f"got {registry.healthy_clients.call_count}"
    )
