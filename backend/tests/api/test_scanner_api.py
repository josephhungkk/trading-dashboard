from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.ws_auth import require_jwt
from app.core.db import engine
from app.main import app
from app.services.config_cache import ConfigCache
from app.services.scanner.scanner_service import ScannerService


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest.fixture(autouse=True)
async def _scanner_api_state(redis: object) -> AsyncIterator[None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[require_jwt] = lambda: "scanner-test@example.com"
    app.state.scanner_service = ScannerService(
        db_factory=factory,
        redis=redis,
        cfg=ConfigCache(redis, "config:invalidate", "config", ttl_seconds=10),
    )
    app.state.scanner_scheduler = AsyncMock()
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)
        app.state.scanner_service = None
        app.state.scanner_scheduler = None


@pytest.mark.asyncio
async def test_validate_valid_expr(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": "rsi(14) < 30 and volume_ratio(20) > 2.0"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.asyncio
async def test_validate_invalid_expr(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": "rsi(14 <<< 30"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_validate_budget_exceeded(client: AsyncClient, auth_headers: dict):
    expr = " and ".join(["rsi(14) < 30"] * 130)
    resp = await client.post(
        "/api/scanner/validate",
        json={"rule_expr": expr},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_scan(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/scanner/scans",
        json={
            "name": "RSI scan",
            "universe_config": {"type": "tickers", "params": {"tickers": ["AAPL"]}},
            "rule_expr": "rsi(14) < 30",
            "llm_depth": "quick",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert "id" in resp.json()


@pytest.mark.asyncio
async def test_list_scans(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/scanner/scans", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
