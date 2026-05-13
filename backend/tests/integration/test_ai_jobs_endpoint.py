"""Phase 11a-C Tasks 24-26: async AI job REST endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.ws_auth import require_jwt
from app.main import app
from app.services.ai.jobs import JobRecord
from app.services.ai.types import CompletionRequest, CompletionResult
from app.services.common.rate_limiter import RateLimitExceededError

pytestmark = pytest.mark.asyncio


class _FakeJobRouter:
    def __init__(self) -> None:
        self.jobs: dict[UUID, JobRecord] = {}
        self.cancelled_job_ids: list[UUID] = []

    async def complete(
        self,
        req: CompletionRequest,
        *,
        jwt_subject: str,
    ) -> CompletionResult:
        raise AssertionError("complete should not be called by /api/ai/jobs")

    async def submit_job(
        self,
        req: CompletionRequest,
        *,
        jwt_subject: str,
    ) -> UUID:
        job_id = uuid4()
        self.jobs[job_id] = _job_record(
            job_id=job_id,
            jwt_subject=jwt_subject,
            status="pending",
            capability=req.capability.value,
        )
        return job_id

    async def get_job(self, job_id: UUID) -> JobRecord | None:
        return self.jobs.get(job_id)

    async def cancel_job(self, job_id: UUID) -> None:
        self.cancelled_job_ids.append(job_id)


class _FakeCapabilitySvc:
    def __init__(self, capability_map: dict[str, list[dict[str, str]]]) -> None:
        self._capability_map = capability_map

    async def get_map(self) -> dict[str, list[dict[str, str]]]:
        return self._capability_map


class _FakeRateLimiter:
    @asynccontextmanager
    async def check_and_acquire(
        self,
        jwt_subject: str,
        capability: str,
    ) -> AsyncIterator[None]:
        yield


class _RateLimitedFakeRateLimiter:
    @asynccontextmanager
    async def check_and_acquire(
        self,
        jwt_subject: str,
        capability: str,
    ) -> AsyncIterator[None]:
        raise RateLimitExceededError("rate limited")
        yield


@pytest.fixture
async def authed_client() -> AsyncIterator[AsyncClient]:
    async def _jwt_subject() -> str:
        return "ci@example.com"

    original_state: dict[str, Any] = {
        "ai_router": getattr(app.state, "ai_router", None),
        "ai_rate_limiter": getattr(app.state, "ai_rate_limiter", None),
        "capability_svc": getattr(app.state, "capability_svc", None),
    }
    missing = {name for name in original_state if not hasattr(app.state, name)}

    app.state.ai_router = _FakeJobRouter()
    app.state.ai_rate_limiter = _FakeRateLimiter()
    app.state.capability_svc = _FakeCapabilitySvc(
        {
            "LOCAL_ONLY": [{"provider": "ollama-nuc", "model": "llama3.1"}],
        }
    )
    app.dependency_overrides[require_jwt] = _jwt_subject

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()
    for name, value in original_state.items():
        if name in missing:
            try:
                delattr(app.state, name)
            except AttributeError:
                pass
        else:
            setattr(app.state, name, value)


@pytest.fixture
async def fake_router(authed_client: AsyncClient) -> _FakeJobRouter:
    return app.state.ai_router


@pytest.fixture
async def authed_client_with_empty_local_capability_map(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.capability_svc = _FakeCapabilitySvc({})
    return authed_client


@pytest.fixture
async def authed_client_rate_limited(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.ai_rate_limiter = _RateLimitedFakeRateLimiter()
    return authed_client


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


def _job_record(
    *,
    job_id: UUID,
    jwt_subject: str,
    status: str = "completed",
    capability: str = "CODING",
) -> JobRecord:
    started_at = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return JobRecord(
        id=job_id,
        jwt_subject=jwt_subject,
        status=status,
        capability=capability,
        request_jsonb={"messages": [{"role": "user", "content": "hi"}]},
        response_jsonb={"text": "done"},
        error=None,
        started_at=started_at,
        warming_started_at=datetime(2026, 5, 13, 10, 0, 1, tzinfo=UTC),
        inferring_started_at=datetime(2026, 5, 13, 10, 0, 2, tzinfo=UTC),
        completed_at=datetime(2026, 5, 13, 10, 0, 3, tzinfo=UTC),
        cancel_requested=False,
    )
