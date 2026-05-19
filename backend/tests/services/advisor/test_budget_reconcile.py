import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from app.services.advisor import budget_reconcile

pytestmark = pytest.mark.no_db


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _AsyncDbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_reconcile_mocks(actual_usd, optimistic_cents):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ScalarResult(actual_usd))

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=optimistic_cents)
    redis.set = AsyncMock()

    return redis, db


@pytest.mark.asyncio
async def test_reconcile_sets_actual_cents_and_negative_delta():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(Decimal("0.50"), 75)

    with patch.object(budget_reconcile.advisor_budget_reconcile_delta_usd, "set") as metric_set:
        await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[1] == 50
    metric_set.assert_called_once_with(-0.25)


@pytest.mark.asyncio
async def test_reconcile_sets_zero_when_no_actual_or_redis_value():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(0, None)

    with patch.object(budget_reconcile.advisor_budget_reconcile_delta_usd, "set") as metric_set:
        await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[1] == 0
    metric_set.assert_called_once_with(0.0)


@pytest.mark.asyncio
async def test_reconcile_sets_actual_cents_and_positive_delta():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(Decimal("1.00"), 80)

    with patch.object(budget_reconcile.advisor_budget_reconcile_delta_usd, "set") as metric_set:
        await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[1] == 100
    metric_set.assert_called_once_with(0.2)


@pytest.mark.asyncio
async def test_reconcile_redis_set_uses_two_day_expiry():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(Decimal("0.10"), 10)

    await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    assert redis.set.await_args.kwargs["ex"] == 172800


@pytest.mark.asyncio
async def test_reconcile_builds_advisor_bot_caller():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(Decimal("0.10"), 10)

    await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    _, params = db.execute.await_args.args
    assert params["caller"] == f"advisor:bot:{bot_id}"


@pytest.mark.asyncio
async def test_reconcile_key_includes_todays_iso_date():
    bot_id = uuid4()
    redis, db = _make_reconcile_mocks(Decimal("0.10"), 10)

    await budget_reconcile.reconcile_budget_for_bot(bot_id, redis, db)

    expected_date = budget_reconcile.date.today().isoformat()
    assert redis.get.await_args.args[0] == f"advisor:spend_estimate_cents:{bot_id}:{expected_date}"
    assert redis.set.await_args.args[0] == f"advisor:spend_estimate_cents:{bot_id}:{expected_date}"


@pytest.mark.asyncio
async def test_run_budget_reconcile_loop_reconciles_each_active_bot_then_sleeps():
    bot_ids = [uuid4(), uuid4()]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_RowsResult([(bot_ids[0],), (bot_ids[1],)]))
    db_factory = MagicMock(return_value=_AsyncDbContext(db))
    redis = AsyncMock()

    with (
        patch.object(budget_reconcile, "reconcile_budget_for_bot", new=AsyncMock()) as reconcile,
        patch.object(
            budget_reconcile.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            await budget_reconcile.run_budget_reconcile_loop(MagicMock(), db_factory, redis)

    reconcile.assert_has_awaits([call(bot_ids[0], redis, db), call(bot_ids[1], redis, db)])
    sleep.assert_awaited_once_with(300)


@pytest.mark.asyncio
async def test_run_budget_reconcile_loop_cancelled_error_propagates_from_outer_try():
    class _CancelledDbContext:
        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db_factory = MagicMock(return_value=_CancelledDbContext())

    with patch.object(budget_reconcile.asyncio, "sleep", new=AsyncMock()) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await budget_reconcile.run_budget_reconcile_loop(MagicMock(), db_factory, AsyncMock())

    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_budget_reconcile_loop_bot_exception_does_not_abort_loop():
    bot_ids = [uuid4(), uuid4()]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_RowsResult([(bot_ids[0],), (bot_ids[1],)]))
    db_factory = MagicMock(return_value=_AsyncDbContext(db))
    redis = AsyncMock()

    reconcile = AsyncMock(side_effect=[RuntimeError("first bot failed"), None])
    with (
        patch.object(budget_reconcile, "reconcile_budget_for_bot", new=reconcile),
        patch.object(
            budget_reconcile.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            await budget_reconcile.run_budget_reconcile_loop(MagicMock(), db_factory, redis)

    reconcile.assert_has_awaits([call(bot_ids[0], redis, db), call(bot_ids[1], redis, db)])
    sleep.assert_awaited_once_with(300)


@pytest.mark.asyncio
async def test_run_budget_reconcile_loop_outer_exception_does_not_abort_loop():
    db_factory = MagicMock(side_effect=RuntimeError("db unavailable"))

    with patch.object(
        budget_reconcile.asyncio,
        "sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await budget_reconcile.run_budget_reconcile_loop(MagicMock(), db_factory, AsyncMock())

    sleep.assert_awaited_once_with(300)
