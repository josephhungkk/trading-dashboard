"""Tests for GET /api/accounts/{id}/{summary,positions,orders}.

AccountService is overridden with an in-memory stub. The maintenance
classifier is monkeypatched on the accounts module to force deterministic
classification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_account_service, require_admin_jwt
from app.main import app
from app.services.brokers import (
    AccountNotFound,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)
from app.services.ibkr_maintenance import BrokerMaintenance


def _money(value: str = "0", currency: str = "USD") -> base.Money:
    return base.Money(value=value, currency=currency)


def _summary() -> base.Summary:
    return base.Summary(
        net_liquidation=_money("100000", "USD"),
        total_cash=_money("50000", "USD"),
        realized_pnl=_money("0", "USD"),
        unrealized_pnl=_money("0", "USD"),
        buying_power=_money("100000", "USD"),
        updated_at=datetime(2026, 4, 26, tzinfo=UTC),
    )


def _contract(symbol: str = "AAPL") -> base.Contract:
    return base.Contract(
        symbol=symbol,
        exchange="NASDAQ",
        currency="USD",
        asset_class="STOCK",
        conid="265598",
        local_symbol=symbol,
    )


def _position() -> base.Position:
    return base.Position(
        contract=_contract(),
        quantity="100",
        avg_cost=_money("150", "USD"),
        market_price=_money("160", "USD"),
        market_value=_money("16000", "USD"),
        unrealized_pnl=_money("1000", "USD"),
        realized_pnl_today=_money("0", "USD"),
        daily_pnl=_money("50", "USD"),
    )


def _order() -> base.Order:
    return base.Order(
        order_id="42",
        contract=_contract(),
        side="BUY",
        order_type="LIMIT",
        quantity="100",
        limit_price=_money("150", "USD"),
        stop_price=_money("0", "USD"),
        time_in_force="DAY",
        status="SUBMITTED",
        quantity_filled="0",
        avg_fill_price=_money("0", "USD"),
        submitted_at=datetime(2026, 4, 26, tzinfo=UTC),
        updated_at=None,
    )


class _StubAccountService:
    """In-memory replacement: pre-loaded with one account; raises AccountNotFound
    for any other UUID. Methods raise the configured exception when set."""

    def __init__(self, account_id: UUID) -> None:
        self._account_id = account_id
        self.raise_exc: Exception | None = None

    async def get_summary(self, account_id: UUID) -> base.Summary:
        if account_id != self._account_id:
            raise AccountNotFound(f"account {account_id} not found")
        if self.raise_exc is not None:
            raise self.raise_exc
        return _summary()

    async def get_positions(self, account_id: UUID) -> list[base.Position]:
        if account_id != self._account_id:
            raise AccountNotFound(f"account {account_id} not found")
        if self.raise_exc is not None:
            raise self.raise_exc
        return [_position()]

    async def get_orders(self, account_id: UUID) -> list[base.Order]:
        if account_id != self._account_id:
            raise AccountNotFound(f"account {account_id} not found")
        if self.raise_exc is not None:
            raise self.raise_exc
        return [_order()]


@pytest.fixture
async def detail_client() -> AsyncIterator[tuple[AsyncClient, _StubAccountService, UUID]]:
    account_id = uuid4()
    stub = _StubAccountService(account_id)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_account_service] = lambda: stub

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, stub, account_id

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _force_outside_reset_window(monkeypatch):
    """Default: never inside any reset window. Tests that need to exercise the
    maintenance branch override this with their own monkeypatch."""
    monkeypatch.setattr(
        "app.api.accounts.compute_broker_maintenance",
        lambda _now: BrokerMaintenance(active=False, window=None, until=None),
    )


# ---------------------------------------------------------------- summary 200/404


@pytest.mark.asyncio
async def test_summary_200(detail_client):
    client, _stub, account_id = detail_client
    resp = await client.get(f"/api/accounts/{account_id}/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["net_liquidation"] == {"value": "100000", "currency": "USD"}


@pytest.mark.asyncio
async def test_summary_404_unknown_uuid(detail_client):
    client, _stub, _ = detail_client
    missing = uuid4()
    resp = await client.get(f"/api/accounts/{missing}/summary")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "detail": f"account {missing}"}


# ------------------------------------------------- summary 503 sidecar_unreachable


@pytest.mark.asyncio
async def test_summary_503_sidecar_unreachable(detail_client):
    client, stub, account_id = detail_client
    stub.raise_exc = BrokerSidecarUnavailable("connect refused", label="isa-live")

    resp = await client.get(f"/api/accounts/{account_id}/summary")

    assert resp.status_code == 503
    assert resp.json() == {"error": "sidecar_unreachable", "label": "isa-live"}
    assert resp.headers["retry-after"] == "30"


@pytest.mark.asyncio
async def test_summary_503_timeout_classified_as_unreachable(detail_client):
    client, stub, account_id = detail_client
    stub.raise_exc = BrokerSidecarTimeout("deadline exceeded")

    resp = await client.get(f"/api/accounts/{account_id}/summary")

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "sidecar_unreachable"
    assert resp.headers["retry-after"] == "30"


# ------------------------------------------------------ summary 503 broker_maintenance


@pytest.mark.asyncio
async def test_summary_503_weekend_maintenance(detail_client, monkeypatch):
    until = datetime(2026, 5, 2, 3, tzinfo=UTC)
    monkeypatch.setattr(
        "app.api.accounts.compute_broker_maintenance",
        lambda _now: BrokerMaintenance(active=True, window="weekend", until=until),
    )

    client, stub, account_id = detail_client
    stub.raise_exc = BrokerSidecarUnavailable("connect refused", label="isa-live")

    resp = await client.get(f"/api/accounts/{account_id}/summary")

    assert resp.status_code == 503
    body = resp.json()
    assert body == {
        "detail": "IBKR weekend maintenance window in progress",
        "broker_maintenance": {
            "active": True,
            "window": "weekend",
            "until": "2026-05-02T03:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_summary_503_daily_maintenance(detail_client, monkeypatch):
    until = datetime(2026, 4, 26, 8, 45, tzinfo=UTC)
    monkeypatch.setattr(
        "app.api.accounts.compute_broker_maintenance",
        lambda _now: BrokerMaintenance(active=True, window="daily", until=until),
    )

    client, stub, account_id = detail_client
    stub.raise_exc = BrokerSidecarTimeout("deadline exceeded")

    resp = await client.get(f"/api/accounts/{account_id}/summary")

    assert resp.status_code == 503
    body = resp.json()
    assert body == {
        "detail": "IBKR daily maintenance window in progress",
        "broker_maintenance": {
            "active": True,
            "window": "daily",
            "until": "2026-04-26T08:45:00Z",
        },
    }


# ------------------------------------------------------------- positions / orders


@pytest.mark.asyncio
async def test_positions_200(detail_client):
    client, _stub, account_id = detail_client
    resp = await client.get(f"/api/accounts/{account_id}/positions")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["contract"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_positions_503_sidecar_unreachable(detail_client):
    client, stub, account_id = detail_client
    stub.raise_exc = BrokerSidecarUnavailable("connect refused", label="normal-paper")

    resp = await client.get(f"/api/accounts/{account_id}/positions")

    assert resp.status_code == 503
    assert resp.json() == {"error": "sidecar_unreachable", "label": "normal-paper"}


@pytest.mark.asyncio
async def test_orders_200(detail_client):
    client, _stub, account_id = detail_client
    resp = await client.get(f"/api/accounts/{account_id}/orders")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["order_id"] == "42"


@pytest.mark.asyncio
async def test_orders_404_unknown_uuid(detail_client):
    client, _stub, _ = detail_client
    missing = uuid4()
    resp = await client.get(f"/api/accounts/{missing}/orders")

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"
