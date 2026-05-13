"""Phase 11a-C Task 23: POST /api/ai/complete failure modes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.ws_auth import require_jwt
from app.main import app
from app.services.ai.exceptions import AIToolCallingNotSupportedError
from app.services.ai.types import CompletionRequest, CompletionResult
from app.services.common.rate_limiter import RateLimitExceededError

pytestmark = pytest.mark.asyncio


class _FakeRouter:
    async def complete(
        self,
        req: CompletionRequest,
        *,
        jwt_subject: str,
    ) -> CompletionResult:
        if req.tools is not None:
            raise AIToolCallingNotSupportedError("tool calling is not supported")
        return CompletionResult(
            request_id=uuid4(),
            text=f"ok:{jwt_subject}",
            provider="test-provider",
            model="test-model",
            prompt_tokens=1,
            completion_tokens=2,
            wall_time_ms=3,
        )


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

    app.state.ai_router = _FakeRouter()
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


@pytest.fixture
async def authed_client_with_fake_router(
    authed_client: AsyncClient,
) -> AsyncClient:
    app.state.ai_router = _FakeRouter()
    return authed_client


async def test_complete_rejects_tools_with_501(
    authed_client: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
        "tools": [{"name": "x", "description": "y", "parameters": {}}],
    }
    resp = await authed_client.post("/api/ai/complete", json=body)
    assert resp.status_code == 501
    assert resp.json()["detail"] == "tool_calling_not_yet_supported"


async def test_complete_local_only_with_no_local_returns_503(
    authed_client_with_empty_local_capability_map: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "LOCAL_ONLY",
        "caller": "test",
    }
    resp = await authed_client_with_empty_local_capability_map.post(
        "/api/ai/complete",
        json=body,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "local_models_unavailable"


async def test_complete_rate_limited_returns_429_with_retry_after(
    authed_client_rate_limited: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
    }
    resp = await authed_client_rate_limited.post("/api/ai/complete", json=body)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"


async def test_complete_happy_path_returns_completion_result(
    authed_client_with_fake_router: AsyncClient,
) -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "capability": "CODING",
        "caller": "test",
    }
    resp = await authed_client_with_fake_router.post("/api/ai/complete", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert "request_id" in payload
    assert payload["text"]
    assert payload["provider"]
