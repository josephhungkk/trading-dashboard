"""Phase 10a D7 — /api/admin/risk-limits CRUD integration test.

Verifies:
- CRUD round-trip (POST 201, PUT 200, DELETE 204)
- CSRF nonce enforcement (missing header -> 403)
- BEFORE UPDATE trigger fn_risk_limits_history snapshots OLD row
- Redis pubsub on app_config:invalidate:risk_limits emitted on every mutation

State isolation: each test creates rows scoped by a unique notes marker
and deletes them in finally. The autouse clean_tables fixture in
test_admin_api.py does NOT run here (it's local to that module).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
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
async def admin_client() -> AsyncIterator[tuple[AsyncClient, fakeredis_async.FakeRedis, AsyncMock]]:
    """Inject admin JWT bypass + fakeredis. Returns (client, redis, publish_mock).

    Wraps redis.publish in an AsyncMock that delegates to the real
    fakeredis publish, so tests can assert the pubsub channel was hit
    while still letting any subscribers (none today) receive.
    """
    r = fakeredis_async.FakeRedis(decode_responses=False)
    real_publish = r.publish

    async def _proxied_publish(*args: Any, **kwargs: Any) -> int:
        return await real_publish(*args, **kwargs)

    publish_mock = AsyncMock(side_effect=_proxied_publish)
    r.publish = publish_mock  # type: ignore[method-assign]

    async def override_admin() -> AdminIdentity:
        return AdminIdentity(email="d7@test.local", kind="user", claims={})

    def override_redis() -> fakeredis_async.FakeRedis:
        return r

    app.dependency_overrides[require_admin_jwt] = override_admin
    app.dependency_overrides[get_redis] = override_redis

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, r, publish_mock
    finally:
        app.dependency_overrides.clear()
        await r.aclose()


async def _mint_nonce(client: AsyncClient) -> str:
    """Mint a single-use CSRF nonce via the shared admin issue endpoint."""
    rsp = await client.post("/api/admin/csrf/issue")
    assert rsp.status_code == 200, rsp.text
    return str(rsp.json()["nonce"])


async def _delete_limits_by_notes_marker(marker: str) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM risk_limits WHERE notes LIKE :m"),
            {"m": f"%{marker}%"},
        )
        await s.execute(
            text("DELETE FROM risk_limits_history WHERE notes LIKE :m"),
            {"m": f"%{marker}%"},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_create_update_delete_roundtrip_with_csrf(
    admin_client: tuple[AsyncClient, fakeredis_async.FakeRedis, AsyncMock],
) -> None:
    client, _r, publish_mock = admin_client
    marker = f"d7-crud-{uuid.uuid4().hex[:8]}"
    body_create: dict[str, Any] = {
        "scope_type": "global",
        "scope_id": None,
        "limit_kind": "max_daily_loss_currency_base",
        "limit_value": "1000.00",
        "warn_at_pct": "80.00",
        "is_active": True,
        "notes": f"created {marker}",
    }

    try:
        nonce = await _mint_nonce(client)
        create_rsp = await client.post(
            "/api/admin/risk-limits",
            json=body_create,
            headers={"X-Confirm-Nonce": nonce},
        )
        assert create_rsp.status_code == 201, create_rsp.text
        created = create_rsp.json()
        limit_id = int(created["id"])
        assert created["scope_type"] == "global"
        assert created["scope_id"] is None
        assert created["limit_kind"] == "max_daily_loss_currency_base"
        assert created["updated_by"] == "d7@test.local"

        # Pubsub fired on the spec-mandated channel with {scope_type, scope_id}
        # payload (D9-fix per spec §4 cap-edit invalidation path).
        publish_mock.assert_any_call(
            "app_config:invalidate:risk_limits",
            b'{"scope_type": "global"}',
        )

        # UPDATE: bump limit_value + flip is_active -> assert history row.
        nonce_put = await _mint_nonce(client)
        body_update = {
            **body_create,
            "limit_value": "2000.00",
            "is_active": False,
            "notes": f"updated {marker}",
        }
        put_rsp = await client.put(
            f"/api/admin/risk-limits/{limit_id}",
            json=body_update,
            headers={"X-Confirm-Nonce": nonce_put},
        )
        assert put_rsp.status_code == 200, put_rsp.text
        updated = put_rsp.json()
        assert updated["limit_value"] == "2000.00000000"
        assert updated["is_active"] is False
        # The mutation must have published an invalidation per spec §6.
        assert publish_mock.await_count >= 2

        # History trigger should have left exactly one row (the prior state).
        async with SessionLocal() as s:
            hist = await s.execute(
                text(
                    """
                    SELECT scope_type::text AS scope_type, limit_value,
                           is_active, notes, changed_by
                      FROM risk_limits_history
                     WHERE limit_id = :id
                  ORDER BY history_id DESC
                    """
                ),
                {"id": limit_id},
            )
            history_rows = list(hist.mappings().all())
        assert len(history_rows) == 1
        snapshot = history_rows[0]
        # changed_by is taken from NEW.updated_by (the same operator).
        assert snapshot["changed_by"] == "d7@test.local"
        # Snapshot is the OLD row, so its limit_value is the original 1000.00.
        assert snapshot["limit_value"] == Decimal("1000.00000000")
        assert snapshot["is_active"] is True
        assert marker in snapshot["notes"]

        # DELETE -> 204.
        nonce_del = await _mint_nonce(client)
        del_rsp = await client.delete(
            f"/api/admin/risk-limits/{limit_id}",
            headers={"X-Confirm-Nonce": nonce_del},
        )
        assert del_rsp.status_code == 204
    finally:
        await _delete_limits_by_notes_marker(marker)


@pytest.mark.asyncio
async def test_post_without_csrf_nonce_returns_403(
    admin_client: tuple[AsyncClient, fakeredis_async.FakeRedis, AsyncMock],
) -> None:
    client, _r, _ = admin_client
    body = {
        "scope_type": "global",
        "scope_id": None,
        "limit_kind": "max_daily_loss_currency_base",
        "limit_value": "1.00",
        "is_active": True,
        "notes": "",
    }
    rsp = await client.post("/api/admin/risk-limits", json=body)
    assert rsp.status_code == 403
    assert rsp.json()["detail"]["error"]["code"] == "missing_csrf"


@pytest.mark.asyncio
async def test_update_nonexistent_id_returns_404(
    admin_client: tuple[AsyncClient, fakeredis_async.FakeRedis, AsyncMock],
) -> None:
    client, _r, _ = admin_client
    nonce = await _mint_nonce(client)
    body = {
        "scope_type": "global",
        "scope_id": None,
        "limit_kind": "max_daily_loss_currency_base",
        "limit_value": "1.00",
        "is_active": True,
        "notes": "",
    }
    rsp = await client.put(
        "/api/admin/risk-limits/999999999",
        json=body,
        headers={"X-Confirm-Nonce": nonce},
    )
    assert rsp.status_code == 404
    assert rsp.json()["detail"]["error"] == "risk_limit_not_found"
