"""Phase 11a-B5: PG-backed AI async-job store with pubsub."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import text

from app.core import metrics


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    jwt_subject: str
    status: str
    capability: str
    request_jsonb: dict[str, Any]
    response_jsonb: dict[str, Any] | None
    error: str | None
    started_at: datetime
    warming_started_at: datetime | None
    inferring_started_at: datetime | None
    completed_at: datetime | None
    cancel_requested: bool


class _RedisPublisher(Protocol):
    async def publish(self, channel: str, message: str | bytes) -> int: ...


class AIJobStore:
    def __init__(self, *, session_factory: Any, redis: _RedisPublisher) -> None:
        self._session_factory = session_factory
        self._redis = redis

    async def create_job(
        self,
        *,
        jwt_subject: str,
        capability: str,
        request: dict[str, Any],
    ) -> JobRecord:
        job_id = uuid4()
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    INSERT INTO ai_jobs (
                        id,
                        jwt_subject,
                        status,
                        capability,
                        request_jsonb
                    )
                    VALUES (
                        :id,
                        :jwt_subject,
                        :status,
                        :capability,
                        :request_jsonb
                    )
                    RETURNING *
                    """
                ),
                {
                    "id": str(job_id),
                    "jwt_subject": jwt_subject,
                    "status": "pending",
                    "capability": capability,
                    "request_jsonb": request,
                },
            )
            row = result.mappings().one()
            await session.commit()

        record = _record_from_row(row)
        _inc_in_flight()
        await self._publish_state(record.id, record.status)
        return record

    async def set_warming(self, job_id: UUID) -> None:
        await self._update_and_publish(
            text(
                """
                UPDATE ai_jobs
                SET status = :status,
                    warming_started_at = now()
                WHERE id = :id
                RETURNING *
                """
            ),
            {"id": str(job_id), "status": "warming"},
        )

    async def set_inferring(self, job_id: UUID) -> None:
        await self._update_and_publish(
            text(
                """
                UPDATE ai_jobs
                SET status = :status,
                    inferring_started_at = now()
                WHERE id = :id
                RETURNING *
                """
            ),
            {"id": str(job_id), "status": "inferring"},
        )

    async def set_completed(self, job_id: UUID, *, response: dict[str, Any]) -> None:
        await self._update_and_publish(
            text(
                """
                UPDATE ai_jobs
                SET status = :status,
                    response_jsonb = :response_jsonb,
                    completed_at = now()
                WHERE id = :id
                RETURNING *
                """
            ),
            {
                "id": str(job_id),
                "status": "completed",
                "response_jsonb": response,
            },
            terminal=True,
        )

    async def set_failed(self, job_id: UUID, *, error: str) -> None:
        await self._update_and_publish(
            text(
                """
                UPDATE ai_jobs
                SET status = :status,
                    error = :error,
                    completed_at = now()
                WHERE id = :id
                RETURNING *
                """
            ),
            {"id": str(job_id), "status": "failed", "error": error},
            terminal=True,
        )

    async def cancel_job(self, job_id: UUID) -> None:
        await self._update_and_publish(
            text(
                """
                UPDATE ai_jobs
                SET status = :status,
                    cancel_requested = true,
                    completed_at = now()
                WHERE id = :id
                RETURNING *
                """
            ),
            {"id": str(job_id), "status": "cancelled"},
            terminal=True,
        )

    async def recover_orphans(self) -> int:
        recovered = 0
        async with self._session_factory() as session:
            warming_result = await session.execute(
                text(
                    """
                    UPDATE ai_jobs
                    SET status = 'failed',
                        error = 'orphaned warming job recovered after restart',
                        completed_at = now()
                    WHERE status = 'warming'
                      AND warming_started_at < now() - interval '90 seconds'
                    RETURNING id
                    """
                )
            )
            warming_ids = list(warming_result.scalars().all())

            inferring_result = await session.execute(
                text(
                    """
                    UPDATE ai_jobs
                    SET status = 'failed',
                        error = 'orphaned inferring job recovered after restart',
                        completed_at = now()
                    WHERE status = 'inferring'
                      AND inferring_started_at < now() - interval '600 seconds'
                    RETURNING id
                    """
                )
            )
            inferring_ids = list(inferring_result.scalars().all())
            await session.commit()

        for recovered_id in warming_ids:
            _inc_orphan_recovered("warming")
            await self._publish_state(_uuid(recovered_id), "failed")
            recovered += 1
        for recovered_id in inferring_ids:
            _inc_orphan_recovered("inferring")
            await self._publish_state(_uuid(recovered_id), "failed")
            recovered += 1
        if recovered:
            _dec_in_flight(recovered)
        return recovered

    async def get_job(self, job_id: UUID) -> JobRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT *
                    FROM ai_jobs
                    WHERE id = :id
                    """
                ),
                {"id": str(job_id)},
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _record_from_row(row)

    async def _update_and_publish(
        self,
        statement: Any,
        params: dict[str, Any],
        *,
        terminal: bool = False,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(statement, params)
            row = result.mappings().one_or_none()
            await session.commit()

        if row is None:
            return
        record = _record_from_row(row)
        if terminal:
            _dec_in_flight()
        await self._publish_state(record.id, record.status)

    async def _publish_state(self, job_id: UUID, status: str) -> None:
        await self._redis.publish(
            f"ai:job:{job_id}",
            json.dumps({"job_id": str(job_id), "status": status, "ts": time.time()}),
        )


def _record_from_row(row: Any) -> JobRecord:
    return JobRecord(
        id=_uuid(row["id"]),
        jwt_subject=row["jwt_subject"],
        status=row["status"],
        capability=row["capability"],
        request_jsonb=row["request_jsonb"],
        response_jsonb=row["response_jsonb"],
        error=row["error"],
        started_at=row["started_at"],
        warming_started_at=row["warming_started_at"],
        inferring_started_at=row["inferring_started_at"],
        completed_at=row["completed_at"],
        cancel_requested=row["cancel_requested"],
    )


def _uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _inc_in_flight() -> None:
    gauge = getattr(metrics, "ai_jobs_in_flight", None)
    if gauge is not None:
        gauge.inc()


def _dec_in_flight(amount: int = 1) -> None:
    gauge = getattr(metrics, "ai_jobs_in_flight", None)
    if gauge is not None:
        gauge.dec(amount)


def _inc_orphan_recovered(phase: str) -> None:
    counter = getattr(metrics, "ai_jobs_orphan_recovered_total", None)
    if counter is not None:
        counter.labels(phase=phase).inc()
