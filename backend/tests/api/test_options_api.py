"""Tests for /api/options/* endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.options import get_chain_service, get_exercise_service
from app.core.cf_access import AdminIdentity
from app.core.deps import get_redis, require_admin_jwt
from app.main import app

pytestmark = pytest.mark.no_db


@pytest_asyncio.fixture
async def options_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    chain_svc = AsyncMock()
    chain_svc.get_expirations.return_value = []
    chain_svc.get_chain.return_value = {
        "calls": [],
        "puts": [],
        "source": "none",
        "fetched_at_ms": 0,
        "stale": True,
    }

    exercise_svc = AsyncMock()
    exercise_svc.list_pending.return_value = []

    redis_mock = AsyncMock()
    redis_mock.delete.return_value = 0  # simulate missing/expired nonce → 403

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="admin@example.test",
        kind="cf_access_jwt",
        claims={},
    )
    app.dependency_overrides[get_chain_service] = lambda: chain_svc
    app.dependency_overrides[get_exercise_service] = lambda: exercise_svc
    app.dependency_overrides[get_redis] = lambda: redis_mock

    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_expirations_returns_list(options_client: AsyncClient) -> None:
    chain_svc = app.dependency_overrides[get_chain_service]()
    chain_svc.get_expirations.return_value = [date(2025, 1, 17), date(2025, 2, 21)]

    resp = await options_client.get("/api/options/expirations?symbol=SPY&currency=USD")
    assert resp.status_code == 200
    data = resp.json()
    assert "expiry_dates" in data
    assert len(data["expiry_dates"]) == 2


@pytest.mark.asyncio
async def test_get_chain_returns_structure(options_client: AsyncClient) -> None:
    chain_svc = app.dependency_overrides[get_chain_service]()
    chain_svc.get_chain.return_value = {
        "calls": [],
        "puts": [],
        "source": "ibkr",
        "fetched_at_ms": 0,
        "stale": False,
    }

    resp = await options_client.get("/api/options/chain?symbol=SPY&expiry=2025-01-17&strikes=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "ibkr"
    assert "calls" in data
    assert "puts" in data


@pytest.mark.asyncio
async def test_post_exercise_requires_csrf(options_client: AsyncClient) -> None:
    """POST /api/options/exercise without X-Confirm-Nonce header should return 403."""
    resp = await options_client.post(
        "/api/options/exercise",
        json={
            "account_id": str(uuid.uuid4()),
            "instrument_id": 42,
            "action": "EXERCISE",
            "qty": "1",
            "idempotency_key": str(uuid.uuid4()),
            "csrf_nonce": "ignored-body-field",
            # X-Confirm-Nonce header intentionally omitted — should 403
        },
    )
    assert resp.status_code == 403
