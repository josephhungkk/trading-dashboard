from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.param_tuner.service import BacktestSubmitter, ParamTunerService
from app.services.param_tuner.types import (
    TunerAlreadyActiveError,
    TunerCostCeilingError,
    TunerTrigger,
)


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one.return_value = value
    r.scalar_one_or_none.return_value = value
    return r


def _first_result(row_dict):
    """CursorResult where .mappings().first() returns a plain dict (supports dict() conversion)."""
    r = MagicMock()
    r.mappings.return_value.first.return_value = row_dict
    r.first.return_value = row_dict
    return r


def _empty_result():
    r = MagicMock()
    r.first.return_value = None
    r.mappings.return_value.first.return_value = None
    r.mappings.return_value.all.return_value = []
    return r


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.incrbyfloat.return_value = 0.05
    r.expire.return_value = True
    return r


@pytest.fixture
def mock_ai_client():
    c = AsyncMock()
    c.complete.return_value = MagicMock(
        content=json.dumps(
            {
                "reasoning": "test",
                "candidates": [{"params": {"fast": 10, "slow": 20}}],
            }
        )
    )
    return c


@pytest.fixture
def mock_backtest_submitter():
    bs = AsyncMock(spec=BacktestSubmitter)
    bs.submit.return_value = uuid4()
    bs.queue_depth.return_value = 0
    return bs


@pytest.fixture
def service(mock_ai_client, mock_redis, mock_backtest_submitter):
    db_factory = MagicMock()
    return ParamTunerService(
        ai_client=mock_ai_client,
        redis=mock_redis,
        db_factory=db_factory,
        backtest_submitter=mock_backtest_submitter,
    )


def _make_db(execute_side_effects):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute_side_effects)
    db.commit = AsyncMock()
    return db


class TestTriggerBotNotFound:
    @pytest.mark.asyncio
    async def test_raises_bot_not_found(self, service):
        # MANUAL trigger skips the scheduled_enabled check
        db = _make_db(
            [
                _empty_result(),  # bots SELECT
            ]
        )
        with pytest.raises(ValueError, match="bot_not_found"):
            await service.trigger(uuid4(), TunerTrigger.MANUAL, db)


class TestTriggerShadowBot:
    @pytest.mark.asyncio
    async def test_raises_cannot_tune_shadow_bot(self, service):
        bot_id = uuid4()
        bot_row = {
            "id": str(bot_id),
            "strategy_params": {},
            "strategy_schema": {"fast": "int"},
            "is_shadow": True,
            "deleted_at": None,
        }
        db = _make_db(
            [
                _first_result(bot_row),  # bots SELECT
            ]
        )
        with pytest.raises(ValueError, match="cannot_tune_shadow_bot"):
            await service.trigger(bot_id, TunerTrigger.MANUAL, db)


class TestTriggerMissingSchema:
    @pytest.mark.asyncio
    async def test_raises_when_strategy_schema_is_none(self, service):
        bot_id = uuid4()
        bot_row = {
            "id": str(bot_id),
            "strategy_params": {},
            "strategy_schema": None,
            "is_shadow": False,
            "deleted_at": None,
        }
        db = _make_db(
            [
                _first_result(bot_row),  # bots SELECT
            ]
        )
        with pytest.raises(ValueError, match="strategy_schema_missing"):
            await service.trigger(bot_id, TunerTrigger.MANUAL, db)


class TestTriggerAlreadyActive:
    @pytest.mark.asyncio
    async def test_raises_when_active_suggestion_exists(self, service):
        bot_id = uuid4()
        bot_row = {
            "id": str(bot_id),
            "strategy_params": {"fast": 10},
            "strategy_schema": {"fast": "int"},
            "is_shadow": False,
            "deleted_at": None,
        }
        active_row = {"id": str(uuid4())}
        db = _make_db(
            [
                _first_result(bot_row),  # bots SELECT
                _first_result(active_row),  # FOR UPDATE SKIP LOCKED
            ]
        )
        with pytest.raises(TunerAlreadyActiveError):
            await service.trigger(bot_id, TunerTrigger.MANUAL, db)


class TestTriggerCostCeiling:
    @pytest.mark.asyncio
    async def test_raises_when_cost_ceiling_exceeded(self, service, mock_redis):
        bot_id = uuid4()
        bot_row = {
            "id": str(bot_id),
            "strategy_params": {"fast": 10},
            "strategy_schema": {"fast": "int"},
            "is_shadow": False,
            "deleted_at": None,
        }
        mock_redis.incrbyfloat.return_value = 999.0

        r_committed = _scalar_result(100.0)
        r_ceiling = MagicMock()
        r_ceiling.scalar_one_or_none.return_value = "5.0"

        db = _make_db(
            [
                _first_result(bot_row),  # bots SELECT
                _empty_result(),  # FOR UPDATE SKIP LOCKED (no active)
                r_committed,  # committed cost query
                r_ceiling,  # ceiling query
            ]
        )
        with pytest.raises(TunerCostCeilingError):
            await service.trigger(bot_id, TunerTrigger.MANUAL, db)


class TestTriggerScheduledDisabled:
    @pytest.mark.asyncio
    async def test_raises_when_scheduled_disabled(self, service):
        config_row = {"value": "false"}
        db = _make_db(
            [
                _first_result(config_row),  # app_config scheduled_enabled
            ]
        )
        with pytest.raises(TunerAlreadyActiveError, match="scheduled_disabled"):
            await service.trigger(uuid4(), TunerTrigger.SCHEDULED, db)


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_raises_when_not_ranked(self, service):
        sid = uuid4()
        row = {
            "id": str(sid),
            "bot_id": str(uuid4()),
            "triggered_by": "manual",
            "status": "pending",
            "candidates": "[]",
        }
        db = _make_db([_first_result(row)])
        with pytest.raises(ValueError, match="suggestion_not_ranked"):
            await service.approve(sid, 0, "admin", db, MagicMock())

    @pytest.mark.asyncio
    async def test_approve_raises_on_bad_index(self, service):
        sid = uuid4()
        row = {
            "id": str(sid),
            "bot_id": str(uuid4()),
            "triggered_by": "manual",
            "status": "ranked",
            "candidates": json.dumps(
                [
                    {
                        "params": {"fast": 10},
                        "backtest_result": {"kpi_sharpe": 1.5},
                        "backtest_job_id": None,
                        "rank": 1,
                        "delta_vs_current": {},
                    }
                ]
            ),
        }
        db = _make_db([_first_result(row)])
        with pytest.raises(ValueError, match="candidate_index_out_of_bounds"):
            await service.approve(sid, 99, "admin", db, MagicMock())

    @pytest.mark.asyncio
    async def test_approve_succeeds_and_calls_restart(self, service):
        sid = uuid4()
        bot_id = uuid4()
        row = {
            "id": str(sid),
            "bot_id": str(bot_id),
            "triggered_by": "manual",
            "status": "ranked",
            "candidates": json.dumps(
                [
                    {
                        "params": {"fast": 10},
                        "backtest_result": {"kpi_sharpe": 1.5},
                        "backtest_job_id": None,
                        "rank": 1,
                        "delta_vs_current": {},
                    }
                ]
            ),
        }
        db = _make_db(
            [
                _first_result(row),  # suggestion SELECT
                MagicMock(),  # bots UPDATE
                MagicMock(),  # suggestions UPDATE
                _scalar_result("running"),  # bot status check
            ]
        )
        supervisor = AsyncMock()
        await service.approve(sid, 0, "admin", db, supervisor)
        supervisor.restart.assert_called_once_with(bot_id)

    @pytest.mark.asyncio
    async def test_approve_skips_restart_when_bot_stopped(self, service):
        sid = uuid4()
        bot_id = uuid4()
        row = {
            "id": str(sid),
            "bot_id": str(bot_id),
            "triggered_by": "manual",
            "status": "ranked",
            "candidates": json.dumps(
                [
                    {
                        "params": {"fast": 10},
                        "backtest_result": {"kpi_sharpe": 1.5},
                        "backtest_job_id": None,
                        "rank": 1,
                        "delta_vs_current": {},
                    }
                ]
            ),
        }
        db = _make_db(
            [
                _first_result(row),
                MagicMock(),
                MagicMock(),
                _scalar_result("stopped"),
            ]
        )
        supervisor = AsyncMock()
        await service.approve(sid, 0, "admin", db, supervisor)
        supervisor.restart.assert_not_called()


class TestReject:
    @pytest.mark.asyncio
    async def test_reject_executes_update(self, service):
        sid = uuid4()
        db = _make_db([MagicMock()])
        await service.reject(sid, "admin", db)
        db.execute.assert_called_once()
        db.commit.assert_called_once()


class TestBacktestSubmitterQueueDepth:
    @pytest.mark.asyncio
    async def test_queue_depth_returns_count(self):
        db = AsyncMock()
        db.execute.return_value = _scalar_result(3)
        db.__aenter__ = AsyncMock(return_value=db)
        db.__aexit__ = AsyncMock(return_value=False)

        db_factory = MagicMock()
        db_factory.return_value = db

        submitter = BacktestSubmitter(db_factory)
        depth = await submitter.queue_depth()
        assert depth == 3
