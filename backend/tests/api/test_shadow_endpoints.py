from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.core.cf_access import AdminIdentity
from app.core.deps import require_admin_jwt
from app.main import app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def _bots_auth_override() -> AsyncIterator[None]:
    admin = AdminIdentity(
        email="shadow-test@example.com",
        kind="user",
        claims={},
    )
    app.dependency_overrides[require_jwt] = lambda: "shadow-test@example.com"
    app.dependency_overrides[require_admin_jwt] = lambda: admin
    app.dependency_overrides[consume_confirmation_nonce] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)
        app.dependency_overrides.pop(require_admin_jwt, None)
        app.dependency_overrides.pop(consume_confirmation_nonce, None)


@pytest_asyncio.fixture
async def bots_client(_bots_auth_override: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def _app_state_patch() -> AsyncIterator[None]:
    app.state.db_factory = MagicMock()
    app.state.supervisor = MagicMock()
    try:
        yield
    finally:
        app.state.db_factory = None
        app.state.supervisor = None


@pytest.fixture
def shadow_service_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    from app.services.shadow_promoter import service as shadow_service

    service = MagicMock()
    service.create_shadow = AsyncMock()
    service.get_comparison = AsyncMock()
    service.promote = AsyncMock()
    monkeypatch.setattr(
        shadow_service,
        "ShadowPromoterService",
        MagicMock(return_value=service),
    )
    return service


@pytest.mark.asyncio
async def test_post_shadows_returns_201(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    _app_state_patch: None,
    shadow_service_mock: MagicMock,
) -> None:
    bot_id = uuid4()
    shadow_id = uuid4()
    shadow_service_mock.create_shadow.return_value = shadow_id

    resp = await bots_client.post(
        f"/api/bots/{bot_id}/shadows",
        json={"override_params": {"risk": 1}},
        headers={**auth_headers, "x-confirm-nonce": "nonce"},
    )

    assert resp.status_code == 201
    assert resp.json() == {"shadow_bot_id": str(shadow_id)}


@pytest.mark.asyncio
async def test_get_shadow_comparison_returns_200(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    _app_state_patch: None,
    shadow_service_mock: MagicMock,
) -> None:
    bot_id = uuid4()
    report = MagicMock()
    report.model_dump.return_value = {
        "live_bot_id": str(bot_id),
        "shadows": [],
        "generated_at": "2026-05-19T08:00:00Z",
    }
    shadow_service_mock.get_comparison.return_value = report

    resp = await bots_client.get(f"/api/bots/{bot_id}/shadows/comparison", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["live_bot_id"] == str(bot_id)
    assert resp.json()["shadows"] == []


@pytest.mark.asyncio
async def test_promote_shadow_returns_200(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    _app_state_patch: None,
    shadow_service_mock: MagicMock,
) -> None:
    resp = await bots_client.post(
        f"/api/bots/{uuid4()}/shadows/{uuid4()}/promote",
        headers={**auth_headers, "x-confirm-nonce": "nonce"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    shadow_service_mock.promote.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_shadow_promotions_returns_empty_items(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(f"/api/bots/{uuid4()}/shadow-promotions", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


@pytest.mark.asyncio
async def test_get_backtest_advisor_decisions_returns_empty_items(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(
        f"/api/bots/{uuid4()}/backtests/{uuid4()}/advisor-decisions",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
