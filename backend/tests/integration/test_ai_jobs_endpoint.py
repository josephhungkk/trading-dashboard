"""Phase 11a-C Tasks 24-26: async AI job REST endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_create_job_returns_202_with_uuid(
    authed_client: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
    }
    resp = await authed_client.post("/api/ai/jobs", json=body)
    assert resp.status_code == 202
    UUID(resp.json()["job_id"])


async def test_create_job_rate_limited_returns_429(
    authed_client_rate_limited: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
    }
    resp = await authed_client_rate_limited.post("/api/ai/jobs", json=body)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"


async def test_create_job_rejects_tools_with_501(
    authed_client: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
        "tools": [{"name": "x", "description": "y", "parameters": {}}],
    }
    resp = await authed_client.post("/api/ai/jobs", json=body)
    assert resp.status_code == 501
    assert resp.json()["detail"] == "tool_calling_not_yet_supported"


async def test_create_job_local_only_with_no_local_returns_503(
    authed_client_with_empty_local_capability_map: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "LOCAL_ONLY",
        "caller": "test",
    }
    resp = await authed_client_with_empty_local_capability_map.post(
        "/api/ai/jobs",
        json=body,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "local_models_unavailable"


async def test_get_job_returns_200_for_owner(
    authed_client: AsyncClient,
    fake_router: Any,
) -> None:
    job_id = uuid4()
    fake_router.jobs[job_id] = fake_router.job_record(
        job_id=job_id,
        jwt_subject="ci@example.com",
    )

    resp = await authed_client.get(f"/api/ai/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json() == {
        "id": str(job_id),
        "status": "completed",
        "capability": "CODING",
        "response": {"text": "done"},
        "error": None,
        "started_at": "2026-05-13T10:00:00Z",
        "warming_started_at": "2026-05-13T10:00:01Z",
        "inferring_started_at": "2026-05-13T10:00:02Z",
        "completed_at": "2026-05-13T10:00:03Z",
        "cancel_requested": False,
    }


async def test_get_job_returns_404_for_unknown_id(
    authed_client: AsyncClient,
) -> None:
    resp = await authed_client.get(f"/api/ai/jobs/{uuid4()}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "job_not_found"}


async def test_get_job_returns_404_for_other_jwt_subject(
    authed_client: AsyncClient,
    fake_router: Any,
) -> None:
    job_id = uuid4()
    fake_router.jobs[job_id] = fake_router.job_record(
        job_id=job_id,
        jwt_subject="other@example.com",
    )

    resp = await authed_client.get(f"/api/ai/jobs/{job_id}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "job_not_found"}


async def test_delete_job_returns_204_for_owner_and_calls_cancel(
    authed_client: AsyncClient,
    fake_router: Any,
) -> None:
    job_id = uuid4()
    fake_router.jobs[job_id] = fake_router.job_record(
        job_id=job_id,
        jwt_subject="ci@example.com",
    )

    resp = await authed_client.delete(f"/api/ai/jobs/{job_id}")

    assert resp.status_code == 204
    assert resp.content == b""
    assert fake_router.cancelled_job_ids == [job_id]


async def test_delete_job_returns_404_for_unknown_id(
    authed_client: AsyncClient,
) -> None:
    resp = await authed_client.delete(f"/api/ai/jobs/{uuid4()}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "job_not_found"}


async def test_delete_job_returns_404_for_other_jwt_subject(
    authed_client: AsyncClient,
    fake_router: Any,
) -> None:
    job_id = uuid4()
    fake_router.jobs[job_id] = fake_router.job_record(
        job_id=job_id,
        jwt_subject="other@example.com",
    )

    resp = await authed_client.delete(f"/api/ai/jobs/{job_id}")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "job_not_found"}
    assert fake_router.cancelled_job_ids == []
