from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.ws_auth import require_jwt
from app.core.deps import get_db, require_admin_jwt
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


def _mock_db_factory(rows: list[dict]):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=mock_result)
    return db


@pytest.mark.asyncio
async def test_digest_latest_empty(client: AsyncClient) -> None:
    mock_db = _mock_db_factory([])
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        resp = await client.get("/api/orchestrator/digest/latest")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_digest_latest_trend_badge_improving(client: AsyncClient) -> None:
    bot_id = uuid.uuid4()
    snap_at = datetime.now(tz=UTC)
    row = {
        "bot_id": bot_id,
        "snapshot_at": snap_at,
        "bot_name": "Alpha Bot",
        "sharpe_30d": Decimal("1.0"),
        "sharpe_7d": Decimal("1.2"),
        "max_drawdown": Decimal("0.05"),
        "win_rate": Decimal("0.62"),
        "total_pnl": None,
        "trade_count": 5,
        "advisor_veto_accuracy_1h": Decimal("0.8"),
        "exposure_utilisation": None,
    }
    mock_db = _mock_db_factory([row])
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        resp = await client.get("/api/orchestrator/digest/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trend_badge"] == "▲"
        assert data[0]["bot_name"] == "Alpha Bot"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_digest_latest_trend_badge_degrading(client: AsyncClient) -> None:
    bot_id = uuid.uuid4()
    snap_at = datetime.now(tz=UTC)
    row = {
        "bot_id": bot_id,
        "snapshot_at": snap_at,
        "bot_name": "Beta Bot",
        "sharpe_30d": Decimal("1.0"),
        "sharpe_7d": Decimal("0.8"),
        "max_drawdown": None,
        "win_rate": None,
        "total_pnl": None,
        "trade_count": 3,
        "advisor_veto_accuracy_1h": None,
        "exposure_utilisation": None,
    }
    mock_db = _mock_db_factory([row])
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        resp = await client.get("/api/orchestrator/digest/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["trend_badge"] == "▼"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_digest_history_not_found(client: AsyncClient) -> None:
    mock_db = _mock_db_factory([])
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        resp = await client.get(f"/api/orchestrator/digest/history/{uuid.uuid4()}")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_digest_history_returns_data(client: AsyncClient) -> None:
    bot_id = uuid.uuid4()
    rows = [
        {
            "snapshot_at": datetime.now(tz=UTC),
            "sharpe_30d": Decimal("0.9"),
            "sharpe_7d": Decimal("1.0"),
            "max_drawdown": Decimal("0.08"),
            "trade_count": 4,
        }
        for _ in range(3)
    ]
    mock_db = _mock_db_factory(rows)
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        resp = await client.get(f"/api/orchestrator/digest/history/{bot_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_correlation_no_redis(client: AsyncClient) -> None:
    account_id = uuid.uuid4()
    with patch.object(app.state, "redis", None, create=True):
        resp = await client.get(f"/api/orchestrator/correlation?account_id={account_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_correlation_returns_matrix(client: AsyncClient) -> None:
    account_id = uuid.uuid4()
    matrix = {"1": {"1": 1.0, "2": 0.4}, "2": {"1": 0.4, "2": 1.0}}
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(matrix).encode())
    with patch.object(app.state, "redis", mock_redis, create=True):
        resp = await client.get(f"/api/orchestrator/correlation?account_id={account_id}")
    assert resp.status_code == 200
    assert resp.json() == matrix


@pytest.mark.asyncio
async def test_correlation_key_not_in_redis(client: AsyncClient) -> None:
    account_id = uuid.uuid4()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    with patch.object(app.state, "redis", mock_redis, create=True):
        resp = await client.get(f"/api/orchestrator/correlation?account_id={account_id}")
    assert resp.status_code == 404
