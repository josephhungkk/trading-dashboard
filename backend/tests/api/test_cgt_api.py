from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.ws_auth import require_jwt
from app.core.deps import require_admin_jwt
from app.main import app


@pytest_asyncio.fixture
async def _overrides() -> AsyncIterator[None]:
    app.dependency_overrides[require_jwt] = lambda: "test@example.com"
    app.dependency_overrides[require_admin_jwt] = lambda: type(
        "AdminIdentity", (), {"sub": "test@example.com", "is_admin": True}
    )()
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)
        app.dependency_overrides.pop(require_admin_jwt, None)


@pytest_asyncio.fixture
async def client(_overrides: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _mock_db(rows=None, scalar_val=None):
    """Build a mock db_factory that returns given rows from execute()."""
    factory = MagicMock()
    session = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = MagicMock(
        net_gain=Decimal("0"),
        net_loss=Decimal("0"),
        disposal_count=0,
    )
    result.fetchall.return_value = rows or []
    result.scalar.return_value = scalar_val or Decimal("0")
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = ctx
    return factory


@pytest.mark.asyncio
async def test_get_cgt_summary_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/cgt/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "net_gain_gbp" in data
    assert "annual_exempt_amount_gbp" in data
    assert "account_number" not in str(data)


@pytest.mark.asyncio
async def test_get_s104_pool_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/cgt/pool")
    assert resp.status_code == 200
    data = resp.json()
    assert "positions" in data


@pytest.mark.asyncio
async def test_get_shorts_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/cgt/shorts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_derivatives_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/cgt/derivatives")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_fx_rates_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/admin/cgt/fx-rates")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_statements_returns_200(client: AsyncClient) -> None:
    app.state.db_factory = _mock_db()
    resp = await client.get("/api/admin/cgt/statements")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_pool_seed_validates_input(client: AsyncClient) -> None:
    resp = await client.post("/api/admin/cgt/pool-seed", json={})
    assert resp.status_code == 422
