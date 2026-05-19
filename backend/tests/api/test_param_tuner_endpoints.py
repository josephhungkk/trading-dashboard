from __future__ import annotations

import sys
import types
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
        email="param-tuner-test@example.com",
        kind="user",
        claims={},
    )
    app.dependency_overrides[require_jwt] = lambda: "param-tuner-test@example.com"
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
    app.state.ai_client = MagicMock()
    app.state.db_factory = MagicMock()
    app.state.supervisor = MagicMock()
    try:
        yield
    finally:
        app.state.ai_client = None
        app.state.db_factory = None
        app.state.supervisor = None


@pytest.fixture
def param_tuner_service_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    service = MagicMock()
    service.trigger = AsyncMock()
    service.approve = AsyncMock()
    service_class = MagicMock(return_value=service)

    fake_module = types.ModuleType("app.services.param_tuner.service")
    fake_module.__dict__["ParamTunerService"] = service_class
    fake_module.__dict__["BacktestSubmitter"] = MagicMock()

    monkeypatch.setitem(sys.modules, "app.services.param_tuner.service", fake_module)
    return service


@pytest.mark.asyncio
async def test_post_param_suggestions_returns_202(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    _app_state_patch: None,
    param_tuner_service_mock: MagicMock,
) -> None:
    bot_id = uuid4()
    suggestion_id = uuid4()
    param_tuner_service_mock.trigger.return_value = suggestion_id

    resp = await bots_client.post(
        f"/api/bots/{bot_id}/param-suggestions",
        headers={**auth_headers, "x-confirm-nonce": "nonce"},
    )

    assert resp.status_code == 202
    assert resp.json() == {"suggestion_id": str(suggestion_id)}


@pytest.mark.asyncio
async def test_get_param_suggestions_returns_empty_items(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(f"/api/bots/{uuid4()}/param-suggestions", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


@pytest.mark.asyncio
async def test_get_param_suggestion_not_found(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.get(
        f"/api/bots/{uuid4()}/param-suggestions/{uuid4()}",
        headers=auth_headers,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "suggestion_not_found"


@pytest.mark.asyncio
async def test_approve_param_suggestion_requires_candidate_index(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
    _app_state_patch: None,
    param_tuner_service_mock: MagicMock,
) -> None:
    resp = await bots_client.post(
        f"/api/bots/{uuid4()}/param-suggestions/{uuid4()}/approve",
        json={},
        headers={**auth_headers, "x-confirm-nonce": "nonce"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "candidate_index_required"
    param_tuner_service_mock.approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_param_suggestion_returns_200(
    bots_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    resp = await bots_client.post(
        f"/api/bots/{uuid4()}/param-suggestions/{uuid4()}/reject",
        headers={**auth_headers, "x-confirm-nonce": "nonce"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
