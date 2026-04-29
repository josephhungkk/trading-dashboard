"""Tests for GET /api/brokers/accounts (Windows tray probe).

The tray polls this every few seconds to render IBKR Live / IBKR Paper
indicators. AccountService is overridden with an in-memory stub so the
route is exercised without broker_accounts or a registry sidecar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import get_account_service, require_admin_jwt
from app.main import app


class _StubAccountService:
    def __init__(self, statuses: list[base.BrokerSidecarStatus]) -> None:
        self._statuses = statuses

    async def list_broker_status(self) -> list[base.BrokerSidecarStatus]:
        return list(self._statuses)


@pytest.fixture
async def client_with_stub() -> AsyncIterator[AsyncClient]:
    statuses = [
        base.BrokerSidecarStatus(broker="ibkr", label="isa-live", mode="live", connected=True),
        base.BrokerSidecarStatus(broker="ibkr", label="isa-paper", mode="paper", connected=True),
        base.BrokerSidecarStatus(broker="ibkr", label="normal-live", mode="live", connected=False),
        base.BrokerSidecarStatus(
            broker="ibkr", label="normal-paper", mode="paper", connected=False
        ),
    ]
    stub = _StubAccountService(statuses)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_account_service] = lambda: stub

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_returns_tray_shape(client_with_stub):
    resp = await client_with_stub.get("/api/brokers/accounts")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"accounts"}
    assert len(body["accounts"]) == 4
    for entry in body["accounts"]:
        assert set(entry.keys()) == {"broker", "label", "mode", "connected"}


@pytest.mark.asyncio
async def test_connected_flag_propagates(client_with_stub):
    resp = await client_with_stub.get("/api/brokers/accounts")
    body = resp.json()

    by_label = {entry["label"]: entry for entry in body["accounts"]}
    assert by_label["isa-live"]["connected"] is True
    assert by_label["isa-paper"]["connected"] is True
    assert by_label["normal-live"]["connected"] is False
    assert by_label["normal-paper"]["connected"] is False


@pytest.mark.asyncio
async def test_modes_match_labels(client_with_stub):
    resp = await client_with_stub.get("/api/brokers/accounts")
    body = resp.json()

    for entry in body["accounts"]:
        if "live" in entry["label"]:
            assert entry["mode"] == "live"
        if "paper" in entry["label"]:
            assert entry["mode"] == "paper"
        assert entry["broker"] == "ibkr"
