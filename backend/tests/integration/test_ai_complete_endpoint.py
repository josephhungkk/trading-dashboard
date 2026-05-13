"""Phase 11a-C Task 23: POST /api/ai/complete failure modes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


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
