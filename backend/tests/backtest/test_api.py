from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

_JWT_SUBJECT = "backtest-test@example.com"


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Cf-Access-Jwt-Assertion": "test-token"}


@pytest_asyncio.fixture
async def _backtest_auth_override() -> AsyncIterator[None]:
    from app.api.ws_auth import require_jwt

    app.dependency_overrides[require_jwt] = lambda: _JWT_SUBJECT
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_jwt, None)


@pytest_asyncio.fixture
async def backtest_client(_backtest_auth_override) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def sample_bot_id(backtest_client: AsyncClient, auth_headers: dict) -> str:
    resp = await backtest_client.post(
        "/api/bots",
        json={
            "name": "Backtest Bot",
            "strategy_file": "test_strategy.py",
            "params_json": {},
            "bar_timeframe": "1d",
            "mode": "paper",
            "account_ids": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_submit_missing_canonical_id(
    backtest_client: AsyncClient, auth_headers, sample_bot_id
):
    resp = await backtest_client.post(
        f"/api/bots/{sample_bot_id}/backtests",
        json={
            "timeframe": "1d",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "slippage_bps": 5.0,
            "bars_source": "db",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_both_slippage_fields_rejected(
    backtest_client: AsyncClient, auth_headers, sample_bot_id
):
    resp = await backtest_client.post(
        f"/api/bots/{sample_bot_id}/backtests",
        json={
            "canonical_id": "AAPL",
            "timeframe": "1d",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "slippage_bps": 5.0,
            "slippage_atr_pct": 0.1,
            "bars_source": "db",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_backtest_returns_404(
    backtest_client: AsyncClient, auth_headers, sample_bot_id
):
    resp = await backtest_client.get(
        f"/api/bots/{sample_bot_id}/backtests/{uuid4()}",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_backtests_cursor_paginated(
    backtest_client: AsyncClient, auth_headers, sample_bot_id
):
    resp = await backtest_client.get(
        f"/api/bots/{sample_bot_id}/backtests",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "next_cursor" in data
