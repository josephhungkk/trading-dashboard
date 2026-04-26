"""Tests for GET /api/accounts and PATCH /api/accounts/{id}.

AccountService is overridden with an in-memory stub so these tests don't
touch broker_accounts or any sidecar — the route layer is what's under
test (response shape, error envelopes, alias validation).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_account_service, require_admin_jwt
from app.main import app
from app.services.brokers import AccountNotFound


class _StubAccountService:
    """In-memory AccountService replacement covering only the methods
    Task 36 routes call (list_accounts, update_alias)."""

    def __init__(
        self,
        accounts: list[base.AccountResponse],
        degraded: list[str] | None = None,
    ) -> None:
        self._by_id: dict[UUID, base.AccountResponse] = {a.id: a for a in accounts}
        self._degraded = degraded or []

    async def list_accounts(self) -> base.AccountListResponse:
        return base.AccountListResponse(
            accounts=list(self._by_id.values()),
            degraded_sidecars=list(self._degraded),
        )

    async def update_alias(
        self,
        account_id: UUID,
        update: base.AccountAliasUpdate,
    ) -> base.AccountResponse:
        existing = self._by_id.get(account_id)
        if existing is None:
            raise AccountNotFound(f"account {account_id} not found")
        updated = existing.model_copy(update={"alias": update.alias})
        self._by_id[account_id] = updated
        return updated


def _make_account(
    *,
    alias: str | None = "ISA Live",
    broker_id: str = "ibkr",
    mode: str = "live",
    display_order: int = 0,
) -> base.AccountResponse:
    return base.AccountResponse(
        id=uuid4(),
        broker_id=broker_id,  # type: ignore[arg-type]
        alias=alias,
        mode=mode,  # type: ignore[arg-type]
        currency_base="USD",
        display_order=display_order,
    )


@pytest.fixture
async def client_with_stub() -> AsyncIterator[tuple[AsyncClient, _StubAccountService]]:
    accounts = [
        _make_account(alias="ISA Live", display_order=0),
        _make_account(alias="ISA Paper", mode="paper", display_order=1),
    ]
    stub = _StubAccountService(accounts, degraded=["normal-paper"])

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_account_service] = lambda: stub

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, stub

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_returns_accounts_and_degraded_labels(client_with_stub):
    client, _stub = client_with_stub
    resp = await client.get("/api/accounts")

    assert resp.status_code == 200
    body = resp.json()
    assert {"accounts", "degraded_sidecars"} == set(body.keys())
    assert body["degraded_sidecars"] == ["normal-paper"]
    assert len(body["accounts"]) == 2


@pytest.mark.asyncio
async def test_list_strips_gateway_label_and_account_number(client_with_stub):
    client, _stub = client_with_stub
    resp = await client.get("/api/accounts")

    body = resp.json()
    for account in body["accounts"]:
        assert "gateway_label" not in account
        assert "account_number" not in account
        assert {
            "id",
            "broker_id",
            "alias",
            "mode",
            "currency_base",
            "display_order",
        } == set(account.keys())


@pytest.mark.asyncio
async def test_patch_alias_happy_path(client_with_stub):
    client, stub = client_with_stub
    target_id = next(iter(stub._by_id))

    resp = await client.patch(
        f"/api/accounts/{target_id}",
        json={"alias": "Renamed ISA"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(target_id)
    assert body["alias"] == "Renamed ISA"


@pytest.mark.asyncio
async def test_patch_alias_unknown_uuid_returns_404_envelope(client_with_stub):
    client, _stub = client_with_stub
    missing_id = uuid4()

    resp = await client.patch(
        f"/api/accounts/{missing_id}",
        json={"alias": "Whatever"},
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body == {"error": "not_found", "detail": f"account {missing_id}"}


@pytest.mark.asyncio
async def test_patch_alias_too_long_rejected(client_with_stub):
    client, stub = client_with_stub
    target_id = next(iter(stub._by_id))

    resp = await client.patch(
        f"/api/accounts/{target_id}",
        json={"alias": "x" * 65},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_alias_invalid_pattern_rejected(client_with_stub):
    client, stub = client_with_stub
    target_id = next(iter(stub._by_id))

    resp = await client.patch(
        f"/api/accounts/{target_id}",
        json={"alias": "bad/alias"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_alias_empty_rejected(client_with_stub):
    client, stub = client_with_stub
    target_id = next(iter(stub._by_id))

    resp = await client.patch(
        f"/api/accounts/{target_id}",
        json={"alias": ""},
    )

    assert resp.status_code == 422
