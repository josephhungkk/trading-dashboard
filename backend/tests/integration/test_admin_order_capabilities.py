"""Phase 8a retro — POST /api/admin/order-capabilities.

HIGH-1: ON CONFLICT target now requires asset_class in the 4-column PK.
HIGH-6: audit log — identity injected so actor is logged.
HIGH-7: nonce now backed by Redis SETEX/GETDEL (no in-process set).
"""

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
    """Fake Redis that handles SETEX/GET/DELETE for nonce + publish for invalidation."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.publish = AsyncMock(return_value=1)

    async def set(self, key: str, value: str, *, ex: int | None = None, **_: Any) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self._store
        self._store.pop(key, None)
        return int(existed)

    def pubsub(self) -> Any:
        return self


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row

    def first(self) -> object | None:
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
            return _FakeResult(object() if params.get("c") == "MARKET" else None)
        if "FROM time_in_force" in sql:
            return _FakeResult(object() if params.get("c") == "DAY" else None)
        if "SELECT is_supported, notes FROM broker_order_capability" in sql:
            # Simulate no prior row (first insert).
            return _FakeResult(None)
        if "INSERT INTO broker_order_capability" in sql:
            self.upserts.append(params)
            return _FakeResult(None)
        return _FakeResult(None)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


def _make_fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def admin_overrides() -> Iterator[None]:
    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="admin@test.local", kind="user", claims={})

    fake_redis = _make_fake_redis()

    def override_redis() -> _FakeRedis:
        return fake_redis

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
        assert nonce_rsp.status_code == 200, nonce_rsp.text
        rsp = await client.post(
            "/api/admin/order-capabilities",
            json=body,
            headers={"X-Confirm-Nonce": str(nonce_rsp.json()["nonce"])},
        )
        return rsp.status_code, rsp.json()


def test_post_full_row_with_asset_class_succeeds(admin_overrides: None) -> None:
    """HIGH-1: asset_class field accepted — 200."""
    body = {
        "broker_id": "ibkr",
        "asset_class": "STOCK",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "tweaked by operator",
    }
    status_code, payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 200, payload


def test_post_missing_asset_class_rejected_422(admin_overrides: None) -> None:
    """HIGH-1: asset_class is required — missing → 422."""
    body = {
        "broker_id": "ibkr",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "",
    }
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 422


def test_post_unknown_asset_class_rejected_422(admin_overrides: None) -> None:
    """HIGH-1: unknown asset_class → 422 (not in KNOWN_ASSET_CLASSES)."""
    body = {
        "broker_id": "ibkr",
        "asset_class": "WIDGET",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "",
    }
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 422


def test_post_etf_asset_class_accepted(admin_overrides: None) -> None:
    """MED-7: ETF is now in KNOWN_ASSET_CLASSES — must not 422."""
    body = {
        "broker_id": "ibkr",
        "asset_class": "ETF",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "",
    }
    status_code, payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 200, payload


def test_post_partial_body_rejected_422(admin_overrides: None) -> None:
    body = {"broker_id": "ibkr", "is_supported": True}
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 422


def test_post_unknown_order_type_rejected_400(admin_overrides: None) -> None:
    body = {
        "broker_id": "ibkr",
        "asset_class": "STOCK",
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
                    "asset_class": "STOCK",
                    "order_type": "MARKET",
                    "time_in_force": "DAY",
                    "is_supported": True,
                    "notes": "",
                },
            )
            return rsp.status_code

    assert asyncio.run(run()) == 403


def test_post_nonce_single_use(admin_overrides: None) -> None:
    """HIGH-7: Redis-backed nonce is consumed on first use; second use → 403."""

    async def run() -> tuple[int, int]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            nonce_rsp = await client.post("/api/admin/csrf/issue")
            nonce = nonce_rsp.json()["nonce"]
            body = {
                "broker_id": "ibkr",
                "asset_class": "STOCK",
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "is_supported": True,
                "notes": "",
            }
            first = await client.post(
                "/api/admin/order-capabilities",
                json=body,
                headers={"X-Confirm-Nonce": nonce},
            )
            second = await client.post(
                "/api/admin/order-capabilities",
                json=body,
                headers={"X-Confirm-Nonce": nonce},
            )
            return first.status_code, second.status_code

    s1, s2 = asyncio.run(run())
    assert s1 == 200
    assert s2 == 403


def test_post_non_ascii_notes_rejected_400(admin_overrides: None) -> None:
    body = {
        "broker_id": "ibkr",
        "asset_class": "STOCK",
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "is_supported": True,
        "notes": "naïve",
    }
    status_code, _payload = asyncio.run(_post_with_nonce(body))
    assert status_code == 400
