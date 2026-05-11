"""Phase 10a D7 — /api/admin/accounts/{id}/kill-switch integration test.

Verifies:
- Toggle POST round-trip (enable then disable) with reason captured
- GET reflects current state; 404 when no row exists
- 'reason required when enabling' enforced (400 on empty reason)
- BEFORE UPDATE trigger fn_account_kill_switches_history snapshots OLD row
- CSRF nonce required on POST

State isolation: each test toggles a real account_id (DB requires an FK
to broker_accounts) and DELETEs the kill-switch row + its history rows
in finally; the broker_accounts row itself is left untouched.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis_async
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.cf_access import AdminIdentity
from app.core.db import SessionLocal
from app.core.deps import get_redis, require_admin_jwt
from app.main import app


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    r = fakeredis_async.FakeRedis(decode_responses=False)
    real_publish = r.publish

    async def _proxied_publish(*args: Any, **kwargs: Any) -> int:
        return await real_publish(*args, **kwargs)

    r.publish = AsyncMock(side_effect=_proxied_publish)  # type: ignore[method-assign]

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="d7-ks@test.local", kind="user", claims={})

    def override_redis() -> fakeredis_async.FakeRedis:
        return r

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_redis] = override_redis

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        await r.aclose()


async def _existing_account_id() -> uuid.UUID:
    """Return any broker_accounts row id; tests assume a populated NUC DB."""
    async with SessionLocal() as s:
        result = await s.execute(text("SELECT id FROM broker_accounts LIMIT 1"))
        row = result.first()
    if row is None:
        pytest.skip("No broker_accounts rows on this DB")
    return row[0]


async def _delete_kill_switch(account_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM account_kill_switches WHERE account_id = :a"),
            {"a": account_id},
        )
        await s.execute(
            text("DELETE FROM account_kill_switches_history WHERE account_id = :a"),
            {"a": account_id},
        )
        await s.commit()


async def _mint_nonce(client: AsyncClient) -> str:
    rsp = await client.post("/api/admin/csrf/issue")
    assert rsp.status_code == 200, rsp.text
    return str(rsp.json()["nonce"])


@pytest.mark.asyncio
async def test_toggle_enable_then_disable_writes_history(
    admin_client: AsyncClient,
) -> None:
    account_id = await _existing_account_id()
    try:
        # Enable.
        nonce = await _mint_nonce(admin_client)
        enable_body: dict[str, Any] = {"is_enabled": True, "reason": "d7 audit test"}
        rsp = await admin_client.post(
            f"/api/admin/accounts/{account_id}/kill-switch",
            json=enable_body,
            headers={"X-Confirm-Nonce": nonce},
        )
        assert rsp.status_code == 200, rsp.text
        enabled = rsp.json()
        assert enabled["is_enabled"] is True
        assert enabled["reason"] == "d7 audit test"
        assert enabled["enabled_by"] == "d7-ks@test.local"
        assert enabled["enabled_at"] is not None

        # GET reflects current state.
        get_rsp = await admin_client.get(f"/api/admin/accounts/{account_id}/kill-switch")
        assert get_rsp.status_code == 200
        assert get_rsp.json()["is_enabled"] is True

        # Disable. enabled_at/enabled_by clear per UPSERT semantics.
        nonce2 = await _mint_nonce(admin_client)
        disable_body = {"is_enabled": False, "reason": ""}
        rsp2 = await admin_client.post(
            f"/api/admin/accounts/{account_id}/kill-switch",
            json=disable_body,
            headers={"X-Confirm-Nonce": nonce2},
        )
        assert rsp2.status_code == 200, rsp2.text
        disabled = rsp2.json()
        assert disabled["is_enabled"] is False
        assert disabled["enabled_at"] is None
        assert disabled["enabled_by"] is None

        # History trigger fired on the UPDATE (disable transition).
        async with SessionLocal() as s:
            result = await s.execute(
                text(
                    """
                    SELECT is_enabled, reason, changed_by
                      FROM account_kill_switches_history
                     WHERE account_id = :a
                  ORDER BY history_id DESC
                    """
                ),
                {"a": account_id},
            )
            rows = list(result.mappings().all())
        # Exactly one history row — the OLD state from the disable UPDATE.
        # The first POST was an INSERT (no trigger fires on INSERT).
        assert len(rows) == 1
        snapshot = rows[0]
        assert snapshot["is_enabled"] is True
        assert snapshot["reason"] == "d7 audit test"
    finally:
        await _delete_kill_switch(account_id)


@pytest.mark.asyncio
async def test_enable_requires_reason(admin_client: AsyncClient) -> None:
    account_id = await _existing_account_id()
    # Empty reason: Pydantic model_validator rejects before reaching service.
    body = {"is_enabled": True, "reason": ""}
    rsp = await admin_client.post(
        f"/api/admin/accounts/{account_id}/kill-switch",
        json=body,
        headers={"X-Confirm-Nonce": await _mint_nonce(admin_client)},
    )
    # Pydantic ValidationError raised at request parse -> FastAPI 422.
    assert rsp.status_code == 422, rsp.text
    # No DB row should have been created.
    async with SessionLocal() as s:
        result = await s.execute(
            text("SELECT 1 FROM account_kill_switches WHERE account_id = :a"),
            {"a": account_id},
        )
        assert result.first() is None


@pytest.mark.asyncio
async def test_get_returns_404_when_no_row(admin_client: AsyncClient) -> None:
    account_id = await _existing_account_id()
    # Ensure no row exists for this account.
    await _delete_kill_switch(account_id)
    rsp = await admin_client.get(f"/api/admin/accounts/{account_id}/kill-switch")
    assert rsp.status_code == 404
    assert rsp.json()["detail"]["error"] == "kill_switch_not_set"


@pytest.mark.asyncio
async def test_post_without_csrf_nonce_returns_403(
    admin_client: AsyncClient,
) -> None:
    account_id = await _existing_account_id()
    body = {"is_enabled": True, "reason": "x"}
    rsp = await admin_client.post(
        f"/api/admin/accounts/{account_id}/kill-switch",
        json=body,
    )
    assert rsp.status_code == 403
    assert rsp.json()["detail"]["error"]["code"] == "missing_csrf"
