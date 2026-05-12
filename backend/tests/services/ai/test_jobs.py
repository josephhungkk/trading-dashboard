"""Phase 11a-B5: tests for the PG-backed AI async-job store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID

import pytest

from app.services.ai import jobs as jobs_module
from app.services.ai.jobs import AIJobStore, JobRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.no_db]


_JOB_ID = UUID("11111111-1111-4111-8111-111111111111")
_STARTED_AT = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
_WARMING_AT = datetime(2026, 5, 12, 10, 1, 0, tzinfo=UTC)
_INFERRING_AT = datetime(2026, 5, 12, 10, 2, 0, tzinfo=UTC)
_COMPLETED_AT = datetime(2026, 5, 12, 10, 3, 0, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": _JOB_ID,
        "jwt_subject": "sub-123",
        "status": "pending",
        "capability": "LOCAL_ONLY",
        "request_jsonb": {"messages": [{"role": "user", "content": "hi"}]},
        "response_jsonb": None,
        "error": None,
        "started_at": _STARTED_AT,
        "warming_started_at": None,
        "inferring_started_at": None,
        "completed_at": None,
        "cancel_requested": False,
    }
    row.update(overrides)
    return row


def _mapping_result(row: dict[str, object] | None) -> MagicMock:
    result = MagicMock()
    mappings = MagicMock()
    result.mappings.return_value = mappings
    mappings.one.return_value = row
    mappings.one_or_none.return_value = row
    return result


def _scalar_result(values: list[UUID]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _session_factory(*results: MagicMock) -> tuple[MagicMock, AsyncMock, MagicMock]:
    session = AsyncMock()
    session.execute.side_effect = list(results)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory, session, cm


def _sql_at(session: AsyncMock, index: int = 0) -> str:
    return str(session.execute.await_args_list[index].args[0])


def _params_at(session: AsyncMock, index: int = 0) -> dict[str, object]:
    return session.execute.await_args_list[index].args[1]


def _published(redis: AsyncMock) -> tuple[str, dict[str, object]]:
    channel, payload = redis.publish.await_args.args
    return channel, json.loads(payload)


async def test_create_job_inserts_and_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_module, "uuid4", lambda: _JOB_ID)
    factory, session, _cm = _session_factory(_mapping_result(_row()))
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    record = await store.create_job(
        jwt_subject="sub-123",
        capability="LOCAL_ONLY",
        request={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert record.id == _JOB_ID
    assert "INSERT INTO ai_jobs" in _sql_at(session)
    params = _params_at(session)
    assert params["id"] == str(_JOB_ID)
    assert params["jwt_subject"] == "sub-123"
    assert params["status"] == "pending"
    assert params["capability"] == "LOCAL_ONLY"
    assert params["request_jsonb"] == {"messages": [{"role": "user", "content": "hi"}]}
    channel, payload = _published(redis)
    assert channel == f"ai:job:{_JOB_ID}"
    assert payload["job_id"] == str(_JOB_ID)
    assert payload["status"] == "pending"
    assert isinstance(payload["ts"], float)


async def test_set_warming_updates_state_and_publishes() -> None:
    factory, session, _cm = _session_factory(_mapping_result(_row(status="warming")))
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    await store.set_warming(_JOB_ID)

    assert "UPDATE ai_jobs" in _sql_at(session)
    assert "status = :status" in _sql_at(session)
    assert "warming_started_at = now()" in _sql_at(session)
    assert _params_at(session)["status"] == "warming"
    assert _params_at(session)["id"] == str(_JOB_ID)
    assert _published(redis)[1]["status"] == "warming"


async def test_set_inferring_updates_state_and_publishes() -> None:
    factory, session, _cm = _session_factory(_mapping_result(_row(status="inferring")))
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    await store.set_inferring(_JOB_ID)

    assert "UPDATE ai_jobs" in _sql_at(session)
    assert "status = :status" in _sql_at(session)
    assert "inferring_started_at = now()" in _sql_at(session)
    assert _params_at(session)["status"] == "inferring"
    assert _published(redis)[1]["status"] == "inferring"


async def test_set_completed_writes_response_and_publishes() -> None:
    response = {"text": "done"}
    factory, session, _cm = _session_factory(
        _mapping_result(_row(status="completed", response_jsonb=response))
    )
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    await store.set_completed(_JOB_ID, response=response)

    assert "UPDATE ai_jobs" in _sql_at(session)
    assert "response_jsonb = :response_jsonb" in _sql_at(session)
    assert "completed_at = now()" in _sql_at(session)
    assert _params_at(session)["status"] == "completed"
    assert _params_at(session)["response_jsonb"] == response
    assert _published(redis)[1]["status"] == "completed"


async def test_set_failed_writes_error_and_publishes() -> None:
    factory, session, _cm = _session_factory(_mapping_result(_row(status="failed", error="boom")))
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    await store.set_failed(_JOB_ID, error="boom")

    assert "UPDATE ai_jobs" in _sql_at(session)
    assert "error = :error" in _sql_at(session)
    assert "completed_at = now()" in _sql_at(session)
    assert _params_at(session)["status"] == "failed"
    assert _params_at(session)["error"] == "boom"
    assert _published(redis)[1]["status"] == "failed"


async def test_cancel_job_sets_flag_and_publishes() -> None:
    factory, session, _cm = _session_factory(
        _mapping_result(_row(status="cancelled", cancel_requested=True))
    )
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    await store.cancel_job(_JOB_ID)

    assert "UPDATE ai_jobs" in _sql_at(session)
    assert "cancel_requested = true" in _sql_at(session)
    assert _params_at(session)["status"] == "cancelled"
    assert _published(redis)[1]["status"] == "cancelled"


async def test_recover_orphans_ages_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    warming_id = UUID("22222222-2222-4222-8222-222222222222")
    inferring_id = UUID("33333333-3333-4333-8333-333333333333")
    factory, session, _cm = _session_factory(
        _scalar_result([warming_id]),
        _scalar_result([inferring_id]),
    )
    redis = AsyncMock()
    counter = MagicMock()
    monkeypatch.setattr(
        jobs_module.metrics,
        "ai_jobs_orphan_recovered_total",
        counter,
        raising=False,
    )
    store = AIJobStore(session_factory=factory, redis=redis)

    recovered = await store.recover_orphans()

    assert recovered == 2
    assert "status = 'warming'" in _sql_at(session, 0)
    assert "warming_started_at < now() - interval '90 seconds'" in _sql_at(session, 0)
    assert "status = 'inferring'" in _sql_at(session, 1)
    assert "inferring_started_at < now() - interval '600 seconds'" in _sql_at(session, 1)
    assert counter.labels.call_args_list == [
        call(phase="warming"),
        call(phase="inferring"),
    ]
    assert counter.labels.return_value.inc.call_count == 2
    assert redis.publish.await_count == 2
    assert redis.publish.await_args_list[0].args[0] == f"ai:job:{warming_id}"
    assert json.loads(redis.publish.await_args_list[0].args[1])["status"] == "failed"
    assert redis.publish.await_args_list[1].args[0] == f"ai:job:{inferring_id}"


async def test_get_job_returns_dataclass() -> None:
    row = _row(
        status="completed",
        response_jsonb={"text": "done"},
        warming_started_at=_WARMING_AT,
        inferring_started_at=_INFERRING_AT,
        completed_at=_COMPLETED_AT,
        cancel_requested=True,
    )
    factory, session, _cm = _session_factory(_mapping_result(row))
    redis = AsyncMock()
    store = AIJobStore(session_factory=factory, redis=redis)

    record = await store.get_job(_JOB_ID)

    assert "SELECT" in _sql_at(session)
    assert "FROM ai_jobs" in _sql_at(session)
    assert _params_at(session)["id"] == str(_JOB_ID)
    assert record == JobRecord(
        id=_JOB_ID,
        jwt_subject="sub-123",
        status="completed",
        capability="LOCAL_ONLY",
        request_jsonb={"messages": [{"role": "user", "content": "hi"}]},
        response_jsonb={"text": "done"},
        error=None,
        started_at=_STARTED_AT,
        warming_started_at=_WARMING_AT,
        inferring_started_at=_INFERRING_AT,
        completed_at=_COMPLETED_AT,
        cancel_requested=True,
    )
