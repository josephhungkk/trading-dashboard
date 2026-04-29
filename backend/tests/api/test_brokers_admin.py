from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, require_admin_jwt
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@pytest.mark.asyncio
async def test_reconfigure_calls_configurer_for_target() -> None:
    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)
    fake_registry = MagicMock(_configurer=fake_configurer)

    async def override_registry() -> MagicMock:
        return fake_registry

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="t@t.com", kind="user", claims={})

    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[require_admin_jwt] = override_admin
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/admin/brokers/futu/reconfigure")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["detail"] == ""
        fake_configurer.configure.assert_called_once_with("futu")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reconfigure_non_target_label_is_noop() -> None:
    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=True)
    fake_registry = MagicMock(_configurer=fake_configurer)

    async def override_registry() -> MagicMock:
        return fake_registry

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="t@t.com", kind="user", claims={})

    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[require_admin_jwt] = override_admin
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/admin/brokers/isa-live/reconfigure")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "does not require" in body["detail"].lower() or "no_target" in body["detail"]
        fake_configurer.configure.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reconfigure_returns_failure_when_configurer_returns_false() -> None:
    fake_configurer = MagicMock()
    fake_configurer.targets = {"futu"}
    fake_configurer.configure = AsyncMock(return_value=False)
    fake_registry = MagicMock(_configurer=fake_configurer)

    async def override_registry() -> MagicMock:
        return fake_registry

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="t@t.com", kind="user", claims={})

    app.dependency_overrides[get_broker_registry] = override_registry
    app.dependency_overrides[require_admin_jwt] = override_admin
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/admin/brokers/futu/reconfigure")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["detail"] == "configure_failed"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reconfigure_requires_admin_jwt() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/brokers/futu/reconfigure")
    assert r.status_code in (401, 403)
