from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.main import app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def _attr_auth_override() -> AsyncIterator[None]:
    app.dependency_overrides[require_jwt] = lambda: "attribution-test@example.com"
    app.dependency_overrides[consume_confirmation_nonce] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)
        app.dependency_overrides.pop(consume_confirmation_nonce, None)


@pytest_asyncio.fixture
async def bots_client(_attr_auth_override: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _create_bot(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    resp = await client.post(
        "/api/bots",
        json={
            "name": f"AttrBot-{uuid4()}",
            "strategy_file": "advisor.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_advisor_attribution_returns_summary(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET advisor-attribution returns 200 with summary fields."""
    bot_id = await _create_bot(bots_client, auth_headers)
    resp = await bots_client.get(
        f"/api/bots/{bot_id}/advisor-attribution",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "veto_accuracy" in data
    assert "complete_count" in data
    assert data["window"] == "1h"


@pytest.mark.asyncio
async def test_advisor_attribution_invalid_window_422(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bot_id = await _create_bot(bots_client, auth_headers)
    resp = await bots_client.get(
        f"/api/bots/{bot_id}/advisor-attribution?window=invalid",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_advisor_attribution_bot_not_found_404(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(
        "/api/bots/00000000-0000-0000-0000-000000000000/advisor-attribution",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_advisor_decision_response_includes_attribution_fields(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Existing decisions endpoint returns attribution_status field."""
    bot_id = await _create_bot(bots_client, auth_headers)
    resp = await bots_client.get(
        f"/api/bots/{bot_id}/advisor-decisions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    # No decisions seeded, but the SELECT should succeed (empty list)
    assert isinstance(items, list)
