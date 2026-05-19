from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def _bots_auth_override() -> AsyncIterator[None]:
    from app.api.ws_auth import require_jwt

    app.dependency_overrides[require_jwt] = lambda: "bots-test@example.com"
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)


@pytest_asyncio.fixture
async def bots_client(_bots_auth_override) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_bot(bots_client: AsyncClient, auth_headers: dict):
    resp = await bots_client.post(
        "/api/bots",
        json={
            "name": "Test Bot",
            "strategy_file": "test_strategy.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "stopped"
    assert data["mode"] == "paper"


@pytest.mark.asyncio
async def test_list_bots(bots_client: AsyncClient, auth_headers: dict):
    resp = await bots_client.get("/api/bots", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_get_bot_not_found(bots_client: AsyncClient, auth_headers: dict):
    resp = await bots_client.get(f"/api/bots/{uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_bot_unknown_account_rejected(bots_client: AsyncClient, auth_headers: dict):
    """bot_accounts FK must reject unknown account_id."""
    resp = await bots_client.post(
        "/api/bots",
        json={
            "name": "Bad Bot",
            "strategy_file": "test.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [str(uuid4())],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_start_bot_sets_status_starting(bots_client: AsyncClient, auth_headers: dict):
    """POST /api/bots/{id}/start sets status=starting and XADDs command."""
    create = await bots_client.post(
        "/api/bots",
        json={
            "name": "StartBot",
            "strategy_file": "s.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert create.status_code == 201
    bot_id = create.json()["id"]

    resp = await bots_client.post(f"/api/bots/{bot_id}/start", headers=auth_headers)
    assert resp.status_code == 200

    detail = await bots_client.get(f"/api/bots/{bot_id}", headers=auth_headers)
    assert detail.json()["status"] == "starting"


@pytest.mark.asyncio
async def test_delete_bot_only_when_stopped(bots_client: AsyncClient, auth_headers: dict):
    create = await bots_client.post(
        "/api/bots",
        json={
            "name": "DelBot",
            "strategy_file": "d.py",
            "params_json": {},
            "bar_timeframe": "1m",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert create.status_code == 201
    bot_id = create.json()["id"]

    resp = await bots_client.delete(f"/api/bots/{bot_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp2 = await bots_client.get(f"/api/bots/{bot_id}", headers=auth_headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_list_strategies(bots_client: AsyncClient, auth_headers: dict, tmp_path, monkeypatch):
    """GET /api/bots/strategies lists .py files in STRATEGIES_DIR."""
    (tmp_path / "my_strategy.py").write_text("# strategy")
    monkeypatch.setenv("STRATEGIES_DIR", str(tmp_path))

    import app.api.bots as bots_module

    original = bots_module._STRATEGIES_DIR
    bots_module._STRATEGIES_DIR = tmp_path
    try:
        resp = await bots_client.get("/api/bots/strategies", headers=auth_headers)
        assert resp.status_code == 200
        names = [s["filename"] for s in resp.json()]
        assert "my_strategy.py" in names
    finally:
        bots_module._STRATEGIES_DIR = original
