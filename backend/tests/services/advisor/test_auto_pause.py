import json
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.services.advisor.auto_pause import AutoPauseService
from app.services.advisor.types import AdvisorConfig

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]


def _config(*, threshold: int = 0, window_seconds: int = 300) -> AdvisorConfig:
    return AdvisorConfig(
        auto_pause_threshold=threshold,
        auto_pause_window_seconds=window_seconds,
    )


def _redis(*, count: int = 0) -> AsyncMock:
    redis = AsyncMock()
    redis.zadd = AsyncMock()
    redis.zremrangebyscore = AsyncMock()
    redis.zcount = AsyncMock(return_value=count)
    redis.xadd = AsyncMock()
    return redis


def _patch_metrics(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    triggered_counter = MagicMock()
    triggered_metric = MagicMock()
    triggered_metric.labels.return_value = triggered_counter
    errors_metric = MagicMock()

    monkeypatch.setattr(
        "app.services.advisor.auto_pause.advisor_auto_pause_triggered_total",
        triggered_metric,
    )
    monkeypatch.setattr(
        "app.services.advisor.auto_pause.advisor_auto_pause_errors_total",
        errors_metric,
    )
    return triggered_metric, triggered_counter, errors_metric


def _xadd_payload(redis: AsyncMock) -> dict:
    _, fields = redis.xadd.await_args.args
    return json.loads(fields["data"])


async def test_record_reject_threshold_zero_does_not_xadd(monkeypatch: pytest.MonkeyPatch):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=1)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=0))

    redis.xadd.assert_not_awaited()


async def test_record_reject_below_threshold_does_not_xadd(monkeypatch: pytest.MonkeyPatch):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=2)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=3))

    redis.xadd.assert_not_awaited()


async def test_record_reject_at_threshold_xadds_pause_command(monkeypatch: pytest.MonkeyPatch):
    triggered_metric, triggered_counter, _ = _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=3)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=3))

    redis.xadd.assert_awaited_once()
    key, fields = redis.xadd.await_args.args
    payload = json.loads(fields["data"])
    assert key == f"bot:control:{bot_id}"
    assert payload["cmd"] == "PAUSE"
    assert payload["reason"] == "advisor_auto_pause"
    triggered_metric.labels.assert_called_once_with(bot_id=str(bot_id))
    triggered_counter.inc.assert_called_once_with()


async def test_record_reject_threshold_one_xadds(monkeypatch: pytest.MonkeyPatch):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=1)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=1))

    redis.xadd.assert_awaited_once()


async def test_zadd_called_with_reject_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.advisor.auto_pause.time.time", lambda: 123.0)
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config())

    key, member = redis.zadd.await_args.args
    assert key == f"bot:advisor:rejects:{bot_id}"
    assert list(member.values()) == [123.0]


async def test_zremrangebyscore_called_with_negative_infinity_and_cutoff(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("app.services.advisor.auto_pause.time.time", lambda: 1_000.0)
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)

    await AutoPauseService(redis).record_reject(
        bot_id=bot_id,
        config=_config(window_seconds=120),
    )

    redis.zremrangebyscore.assert_awaited_once_with(
        f"bot:advisor:rejects:{bot_id}",
        "-inf",
        880.0,
    )


async def test_zcount_called_after_zremrangebyscore(monkeypatch: pytest.MonkeyPatch):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config())

    method_names = [mock_call[0] for mock_call in redis.mock_calls]
    assert method_names.index("zremrangebyscore") < method_names.index("zcount")


async def test_redis_error_on_zadd_is_swallowed_and_error_metric_incremented(
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, errors_metric = _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)
    redis.zadd.side_effect = RuntimeError("redis down")

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=1))

    redis.xadd.assert_not_awaited()
    errors_metric.inc.assert_called_once_with()


async def test_redis_error_on_zcount_is_swallowed(monkeypatch: pytest.MonkeyPatch):
    _, _, errors_metric = _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)
    redis.zcount.side_effect = RuntimeError("redis down")

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=1))

    redis.xadd.assert_not_awaited()
    errors_metric.inc.assert_called_once_with()


async def test_xadd_payload_has_valid_json_with_id_cmd_and_reason(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=1)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=1))

    payload = _xadd_payload(redis)
    assert set(payload) == {"id", "cmd", "reason"}
    assert UUID(payload["id"])
    assert payload["cmd"] == "PAUSE"
    assert payload["reason"] == "advisor_auto_pause"


async def test_threshold_zero_never_xadds_even_with_large_count(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=999)

    await AutoPauseService(redis).record_reject(bot_id=bot_id, config=_config(threshold=0))

    redis.xadd.assert_not_awaited()


async def test_window_seconds_is_used_as_cutoff_when_time_is_mocked(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("app.services.advisor.auto_pause.time.time", lambda: 10_000.0)
    _patch_metrics(monkeypatch)
    bot_id = uuid4()
    redis = _redis(count=0)

    await AutoPauseService(redis).record_reject(
        bot_id=bot_id,
        config=_config(window_seconds=321),
    )

    assert redis.zremrangebyscore.await_args.args == (
        f"bot:advisor:rejects:{bot_id}",
        "-inf",
        9_679.0,
    )
