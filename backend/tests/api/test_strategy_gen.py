from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.core.deps import require_admin_jwt
from app.main import app


@pytest_asyncio.fixture
async def _sg_overrides() -> AsyncIterator[None]:
    app.dependency_overrides[require_jwt] = lambda: "sg-test@example.com"
    app.dependency_overrides[require_admin_jwt] = lambda: type(
        "AdminIdentity", (), {"sub": "sg-admin@example.com", "is_admin": True}
    )()
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)
        app.dependency_overrides.pop(require_admin_jwt, None)


@pytest_asyncio.fixture
async def auth_client(_sg_overrides: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_client(_sg_overrides: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_strategies_empty(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/api/strategy-gen")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_strategy_not_found(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/api/strategy-gen/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_strategy(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/strategy-gen/generate",
        json={"asset_class": "stock", "market_context": "bullish", "llm_model": "gpt-4"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_approve_requires_admin(auth_client: AsyncClient) -> None:
    app.dependency_overrides.pop(require_admin_jwt, None)
    try:
        resp = await auth_client.post("/api/strategy-gen/1/approve", json={"bot_name": "test"})
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_admin_jwt] = lambda: type(
            "AdminIdentity", (), {"sub": "sg-admin@example.com", "is_admin": True}
        )()


@pytest.mark.asyncio
async def test_approve_requires_csrf(admin_client: AsyncClient) -> None:
    app.dependency_overrides.pop(consume_confirmation_nonce, None)
    try:
        resp = await admin_client.post("/api/strategy-gen/1/approve", json={"bot_name": "test"})
        assert resp.status_code == 403
    finally:
        pass  # no override to restore — nonce stays at real implementation


@pytest.mark.asyncio
async def test_approve_validated_strategy(admin_client: AsyncClient, session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "INSERT INTO generated_strategies"
            " (name, source_code, source_hash, generation_prompt,"
            "  prompt_hash, llm_model, sandbox_status)"
            " VALUES ('gen_stock_test', 'code', 'hash1', 'prompt', 'ph1', 'gpt-4', 'validated')"
            " RETURNING id"
        )
    )
    strategy_id = result.scalar_one()
    await session.commit()

    app.dependency_overrides[consume_confirmation_nonce] = lambda: None
    try:
        resp = await admin_client.post(
            f"/api/strategy-gen/{strategy_id}/approve",
            json={"bot_name": "test_bot"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy_id"] == strategy_id
        assert "bot_id" in data
        assert data["status"] == "paper_pending"
    finally:
        app.dependency_overrides.pop(consume_confirmation_nonce, None)


@pytest.mark.asyncio
async def test_reject_strategy(admin_client: AsyncClient, session: AsyncSession) -> None:
    result = await session.execute(
        text(
            "INSERT INTO generated_strategies"
            " (name, source_code, source_hash, generation_prompt,"
            "  prompt_hash, llm_model, sandbox_status)"
            " VALUES ('gen_stock_rej', 'code', 'hash2', 'prompt', 'ph2', 'gpt-4', 'validated')"
            " RETURNING id"
        )
    )
    strategy_id = result.scalar_one()
    await session.commit()

    app.dependency_overrides[consume_confirmation_nonce] = lambda: None
    try:
        resp = await admin_client.post(f"/api/strategy-gen/{strategy_id}/reject")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == strategy_id
        assert data["status"] == "rejected"
    finally:
        app.dependency_overrides.pop(consume_confirmation_nonce, None)
