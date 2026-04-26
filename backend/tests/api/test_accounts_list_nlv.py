"""API-level tests for the list endpoint's new NLV fields (spec §11).

Pins wire format invariants (§3.1 R3+R4) and envelope shape (§3.2).
Uses dependency-override stubs (no DB writes) following the pattern in
test_accounts_list.py — keeps the test surface deterministic and
isolated from prod broker_accounts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_account_service, require_admin_jwt
from app.main import app
from app.services.brokers import _format_nlv

# ---------- _format_nlv unit tests (spec §3.1 R3+R4) ----------


def test_format_nlv_none_returns_none() -> None:
    assert _format_nlv(None) is None


def test_format_nlv_zero_is_fixed_point_8_decimals() -> None:
    out = _format_nlv(Decimal("0"))
    assert out == "0.00000000"
    assert "e" not in out.lower()


def test_format_nlv_one_tenth_keeps_trailing_zeros() -> None:
    assert _format_nlv(Decimal("0.1")) == "0.10000000"


def test_format_nlv_full_precision_preserved() -> None:
    assert _format_nlv(Decimal("12345.67890123")) == "12345.67890123"


def test_format_nlv_max_precision_no_scientific_notation() -> None:
    out = _format_nlv(Decimal("999999999999.99999999"))
    assert out == "999999999999.99999999"
    assert "e" not in out.lower()


def test_format_nlv_tiny_value_no_scientific_notation() -> None:
    out = _format_nlv(Decimal("0.00000001"))
    assert out == "0.00000001"
    assert "e" not in out.lower()


# ---------- Stub + route-level tests (spec §3.2) ----------


class _StubAccountService:
    def __init__(self, accounts: list[base.AccountResponse]) -> None:
        self._accounts = accounts

    async def list_accounts(self) -> base.AccountListResponse:
        return base.AccountListResponse(
            accounts=list(self._accounts),
            degraded_sidecars=[],
            broker_maintenance=base.BrokerMaintenance(active=False, window=None, until=None),
        )


def _make_account(
    *,
    nlv: str | None = None,
    nlv_currency: str | None = None,
    nlv_at: datetime | None = None,
) -> base.AccountResponse:
    return base.AccountResponse(
        id=uuid4(),
        broker_id="ibkr",  # type: ignore[arg-type]
        alias="Test",
        mode="paper",  # type: ignore[arg-type]
        currency_base="USD",
        display_order=0,
        nlv=nlv,
        nlv_currency=nlv_currency,
        nlv_at=nlv_at,
    )


@pytest.fixture
async def client_with_accounts() -> AsyncIterator[tuple[AsyncClient, list[base.AccountResponse]]]:
    populated = _make_account(
        nlv="12345.67890123",
        nlv_currency="USD",
        nlv_at=datetime(2026, 4, 26, 18, 0, 0, tzinfo=UTC),
    )
    unpopulated = _make_account()
    accounts = [populated, unpopulated]
    stub = _StubAccountService(accounts)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_account_service] = lambda: stub

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, accounts

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_account_response_has_nlv_fields_when_populated(client_with_accounts) -> None:
    client, accounts = client_with_accounts
    populated_id = str(accounts[0].id)
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    row = next(a for a in body["accounts"] if a["id"] == populated_id)
    assert row["nlv"] == "12345.67890123"
    assert row["nlv_currency"] == "USD"
    assert row["nlv_at"] is not None


@pytest.mark.asyncio
async def test_account_response_null_nlv_when_unpopulated(client_with_accounts) -> None:
    client, accounts = client_with_accounts
    unpopulated_id = str(accounts[1].id)
    resp = await client.get("/api/accounts")
    body = resp.json()
    row = next(a for a in body["accounts"] if a["id"] == unpopulated_id)
    assert row["nlv"] is None
    assert row["nlv_currency"] is None
    assert row["nlv_at"] is None


@pytest.mark.asyncio
async def test_envelope_carries_broker_maintenance(client_with_accounts) -> None:
    client, _ = client_with_accounts
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert "broker_maintenance" in body
    bm = body["broker_maintenance"]
    assert set(bm.keys()) == {"active", "window", "until"}
    assert isinstance(bm["active"], bool)
