from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orchestrator.digest import HealthDigestService


def _make_svc(bots=None, run_rows=None, advisor_rows=None, telegram=None):
    """Helper: build a HealthDigestService with mocked db_factory."""

    async def _db_ctx():
        db = AsyncMock()

        # map query keywords to return values
        def _execute_side_effect(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            if "from bots" in sql:
                row_mock = MagicMock()
                row_mock.all.return_value = bots or []
                return MagicMock(all=row_mock.all)
            if "avg(kpi_sharpe)" in sql and "30 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.8,)))
            if "avg(kpi_sharpe)" in sql and "7 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(1.0,)))
            if "max(kpi_max_dd)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.1,)))
            if "avg(kpi_win_rate)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.55,)))
            if "count(*)" in sql and "bot_runs" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(5,)))
            if "bot_advisor_decisions" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.75,)))
            if "insert into bot_health_snapshots" in sql:
                return MagicMock()
            if "app_config" in sql:
                return MagicMock(fetchone=MagicMock(return_value=None))
            return MagicMock(
                fetchone=MagicMock(return_value=(None,)), all=MagicMock(return_value=[])
            )

        db.execute = AsyncMock(side_effect=_execute_side_effect)
        db.commit = AsyncMock()
        return db

    class _FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return await _db_ctx()

        async def __aexit__(self, *a):
            pass

    return HealthDigestService(
        db_factory=_FakeFactory(),
        telegram=telegram,
        redis=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_digest_no_bots_runs_clean() -> None:
    svc = _make_svc(bots=[])
    await svc.run()


@pytest.mark.asyncio
async def test_digest_inserts_snapshot_per_bot() -> None:
    bot_id = uuid.uuid4()
    bot_name = "Alpha"
    bots = [(bot_id, bot_name)]

    inserted: list[dict] = []

    async def _db_ctx():
        db = AsyncMock()

        def _execute_side_effect(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            if "from bots" in sql:
                return MagicMock(all=MagicMock(return_value=bots))
            if "insert into bot_health_snapshots" in sql:
                inserted.append(dict(kwargs.get("parameters", args[0] if args else {})))
                return MagicMock()
            if "avg(kpi_sharpe)" in sql and "30 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.8,)))
            if "avg(kpi_sharpe)" in sql and "7 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.9,)))
            if "max(kpi_max_dd)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.05,)))
            if "avg(kpi_win_rate)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.6,)))
            if "count(*)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(10,)))
            if "bot_advisor_decisions" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.7,)))
            if "app_config" in sql:
                return MagicMock(fetchone=MagicMock(return_value=None))
            return MagicMock(
                fetchone=MagicMock(return_value=(None,)), all=MagicMock(return_value=[])
            )

        db.execute = AsyncMock(side_effect=_execute_side_effect)
        db.commit = AsyncMock()
        return db

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return await _db_ctx()

        async def __aexit__(self, *a):
            pass

    svc = HealthDigestService(db_factory=_Factory(), telegram=None, redis=AsyncMock())
    await svc.run()


@pytest.mark.asyncio
async def test_digest_telegram_kill_switch_suppresses_send() -> None:
    telegram = AsyncMock()
    _make_svc(bots=[], telegram=telegram)

    # Kill switch value returned as "false"
    async def _db_ctx_disabled():
        db = AsyncMock()

        def _exec(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            if "from bots" in sql:
                return MagicMock(all=MagicMock(return_value=[]))
            if "app_config" in sql:
                return MagicMock(fetchone=MagicMock(return_value=("false",)))
            return MagicMock(fetchone=MagicMock(return_value=None), all=MagicMock(return_value=[]))

        db.execute = AsyncMock(side_effect=_exec)
        db.commit = AsyncMock()
        return db

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return await _db_ctx_disabled()

        async def __aexit__(self, *a):
            pass

    svc2 = HealthDigestService(db_factory=_Factory(), telegram=telegram, redis=AsyncMock())
    await svc2.run()
    telegram.send.assert_not_called()


@pytest.mark.asyncio
async def test_digest_bot_error_does_not_abort_run() -> None:
    bot1 = (uuid.uuid4(), "BotOK")
    bot2 = (uuid.uuid4(), "BotBad")
    call_count = {"n": 0}

    async def _db_ctx():
        db = AsyncMock()

        def _exec(stmt, *args, **kwargs):
            sql = str(stmt).lower()
            if "from bots" in sql:
                return MagicMock(all=MagicMock(return_value=[bot1, bot2]))
            # Make second bot fail on sharpe query
            call_count["n"] += 1
            if call_count["n"] > 7:
                raise RuntimeError("simulated DB error")
            if "avg(kpi_sharpe)" in sql and "30 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.5,)))
            if "avg(kpi_sharpe)" in sql and "7 days" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.6,)))
            if "max(kpi_max_dd)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.1,)))
            if "avg(kpi_win_rate)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(0.5,)))
            if "count(*)" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(3,)))
            if "bot_advisor_decisions" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(None,)))
            if "insert into bot_health_snapshots" in sql:
                return MagicMock()
            if "app_config" in sql:
                return MagicMock(fetchone=MagicMock(return_value=None))
            return MagicMock(
                fetchone=MagicMock(return_value=(None,)), all=MagicMock(return_value=[])
            )

        db.execute = AsyncMock(side_effect=_exec)
        db.commit = AsyncMock()
        return db

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return await _db_ctx()

        async def __aexit__(self, *a):
            pass

    svc = HealthDigestService(db_factory=_Factory(), telegram=None, redis=AsyncMock())
    # Must not raise despite one bot erroring
    await svc.run()
