"""Phase 8a - POST /api/admin/order-capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.main import app


class _FakeRedis:
    def __init__(self) -> None:
        self.publish = AsyncMock(return_value=1)


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.upserts: list[dict[str, Any]] = []

    async def execute(
        self,
        stmt: object,
        params: dict[str, Any] | None = None,
    ) -> _FakeResult:
        sql = str(stmt)
        params = params or {}
        if "FROM order_types" in sql:
            return _FakeResult(object() if params["c"] == "MARKET" else None)
        if "FROM time_in_force" in sql:
            return _FakeResult(object() if params["c"] == "DAY" else None)
        if "INSERT INTO broker_order_capability" in sql:
            self.upserts.append(params)
            return _FakeResult(None)
        return _FakeResult(None)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@pytest.fixture
def admin_overrides() -> Iterator[None]:
    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="admin@test.local", kind="user", claims={})

    def override_redis() -> _FakeRedis:
        return _FakeRedis()

    async def override_db() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_redis] = override_redis
    app.dependency_overrides[get_db] = override_db
    try:
        yield
    finally:
        app.dependency_overrides.clear()


async def _post_with_nonce(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        nonce_rsp = await client.post("/api/admin/csrf/issue")
        assert nonce_rsp.status_code == 200
        rsp = await client.post(
            "/api/admin/order-capabilities",
            json=body,
            headers={"X-Confirm-Nonce": str(nonce_rsp.json()["nonce"])},
        )
        return rsp.status_code, rsp.json()


def test_post_full_row_succeeds(admin_overrides: None) -> None:
    body = {
        "broker_id": "ibkr",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "tweaked by operator",
    }
    status_code, payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 200, payload


def test_post_partial_body_rejected_400(admin_overrides: None) -> None:
    body = {"broker_id": "ibkr", "is_supported": True}
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 422


def test_post_unknown_order_type_rejected_400(admin_overrides: None) -> None:
    body = {
        "broker_id": "ibkr",
        "order_type": "BLOOP",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "",
    }
    status_code, payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 400
    assert payload["detail"]["error"]["code"] == "unknown_order_type_code"


def test_post_missing_csrf_rejected_403(admin_overrides: None) -> None:
    async def run() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            rsp = await client.post(
                "/api/admin/order-capabilities",
                json={
                    "broker_id": "ibkr",
                    "order_type": "MARKET",
                    "time_in_force": "DAY",
                    "is_supported": True,
                    "notes": "",
                },
            )
            return rsp.status_code

    assert asyncio.run(run()) == 403


def test_post_non_ascii_notes_rejected_400(admin_overrides: None) -> None:
    body = {
        "broker_id": "ibkr",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "naïve",
    }
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 400
