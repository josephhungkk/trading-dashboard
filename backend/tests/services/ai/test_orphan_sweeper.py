"""Phase 11a-C HIGH-8: ai_jobs orphan sweeper tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core import metrics
from app.core.db import SessionLocal
from app.services.ai.orphan_sweeper import sweep_orphans_once

pytestmark = pytest.mark.asyncio

_WARMING_JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
_INFERRING_JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
_UNDER_CUTOFF_JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")


@pytest_asyncio.fixture(autouse=True)
async def clean_ai_jobs() -> AsyncIterator[None]:
    await _delete_ai_jobs()
    yield
    await _delete_ai_jobs()


async def _delete_ai_jobs() -> None:
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM ai_jobs"))
        await session.commit()


async def _insert_job(job_id: UUID, *, status: str, age_seconds: int) -> None:
    timestamp_column = {
        "warming": "warming_started_at",
        "inferring": "inferring_started_at",
    }[status]
    async with SessionLocal() as session:
        await session.execute(
            text(
                f"""
                INSERT INTO ai_jobs (
                    id,
                    jwt_subject,
                    status,
                    capability,
                    request_jsonb,
                    {timestamp_column}
                )
                VALUES (
                    :id,
                    'test-subject',
                    :status,
                    'LOCAL_ONLY',
                    CAST(:request_jsonb AS jsonb),
                    NOW() - make_interval(secs => :age_seconds)
                )
                """
            ),
            {
                "id": str(job_id),
                "status": status,
                "request_jsonb": json.dumps({"messages": []}),
                "age_seconds": age_seconds,
            },
        )
        await session.commit()


async def _fetch_job(job_id: UUID) -> dict[str, object]:
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT status, error, completed_at
                FROM ai_jobs
                WHERE id = :id
                """
            ),
            {"id": str(job_id)},
        )
        return dict(result.mappings().one())


def _orphan_counter_value(phase: str) -> float:
    return metrics.ai_jobs_orphan_recovered_total.labels(phase=phase)._value.get()


async def test_warming_aged_past_cutoff_transitions_to_failed() -> None:
    await _insert_job(_WARMING_JOB_ID, status="warming", age_seconds=100)
    warming_before = _orphan_counter_value("warming")

    recovered = await sweep_orphans_once(SessionLocal)

    assert recovered == 1
    row = await _fetch_job(_WARMING_JOB_ID)
    assert row["status"] == "failed"
    assert row["error"] == "be_restart"
    assert row["completed_at"] is not None
    assert _orphan_counter_value("warming") - warming_before == 1


async def test_inferring_aged_past_cutoff_transitions_to_failed() -> None:
    await _insert_job(_INFERRING_JOB_ID, status="inferring", age_seconds=660)
    inferring_before = _orphan_counter_value("inferring")

    recovered = await sweep_orphans_once(SessionLocal)

    assert recovered == 1
    row = await _fetch_job(_INFERRING_JOB_ID)
    assert row["status"] == "failed"
    assert row["error"] == "be_restart"
    assert row["completed_at"] is not None
    assert _orphan_counter_value("inferring") - inferring_before == 1


async def test_warming_under_cutoff_left_alone() -> None:
    await _insert_job(_UNDER_CUTOFF_JOB_ID, status="warming", age_seconds=30)
    warming_before = _orphan_counter_value("warming")

    recovered = await sweep_orphans_once(SessionLocal)

    assert recovered == 0
    row = await _fetch_job(_UNDER_CUTOFF_JOB_ID)
    assert row["status"] == "warming"
    assert row["error"] is None
    assert row["completed_at"] is None
    assert _orphan_counter_value("warming") - warming_before == 0


async def test_empty_table_no_error() -> None:
    warming_before = _orphan_counter_value("warming")
    inferring_before = _orphan_counter_value("inferring")

    recovered = await sweep_orphans_once(SessionLocal)

    assert recovered == 0
    assert _orphan_counter_value("warming") - warming_before == 0
    assert _orphan_counter_value("inferring") - inferring_before == 0
