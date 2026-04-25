"""Tests for sidecar.handlers Health + ListManagedAccounts + GetAccountSummary (Task 11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from sidecar._generated.broker.v1 import broker_pb2
from sidecar.handlers import BrokerHandlers


@dataclass
class FakeAccountValue:
    """Mirrors ib_async.AccountValue's read surface (account/tag/value/currency)."""

    account: str
    tag: str
    value: str
    currency: str = ""


@dataclass
class FakeIBClient:
    server_version: int = 178

    def serverVersion(self) -> int:  # noqa: N802 — match ib_async API
        return self.server_version


@dataclass
class FakeIB:
    """Minimal ib_async.IB stand-in covering the surface BrokerHandlers touches."""

    managed_accounts: list[str] = field(default_factory=list)
    values: list[FakeAccountValue] = field(default_factory=list)
    connected: bool = True
    raise_on_managed: bool = False
    raise_on_values: bool = False
    raise_on_connected: bool = False
    server_version: int = 178

    def isConnected(self) -> bool:  # noqa: N802
        if self.raise_on_connected:
            raise RuntimeError("socket reset")
        return self.connected

    @property
    def client(self) -> FakeIBClient:
        return FakeIBClient(server_version=self.server_version)

    async def reqManagedAccountsAsync(self) -> list[str]:  # noqa: N802
        if self.raise_on_managed:
            raise RuntimeError("api timeout")
        return list(self.managed_accounts)

    def accountValues(self) -> list[FakeAccountValue]:  # noqa: N802
        if self.raise_on_values:
            raise RuntimeError("subscription not ready")
        return list(self.values)


def _handlers(ib: FakeIB, last_tick_ref: dict[str, datetime] | None = None) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        label="ibgw_live_us",
        version="0.4.0+test",
        last_tick_ref=last_tick_ref or {},
    )


# ---------- Health ----------


@pytest.mark.asyncio
async def test_health_returns_label_and_version_when_connected() -> None:
    ib = FakeIB(connected=True, server_version=178)
    h = _handlers(ib)
    response = await h.Health(broker_pb2.HealthRequest(), context=object())
    assert response.label == "ibgw_live_us"
    assert response.gateway_connected is True
    assert response.gateway_version == "178"
    assert response.sidecar_version == "0.4.0+test"


@pytest.mark.asyncio
async def test_health_omits_gateway_version_when_disconnected() -> None:
    ib = FakeIB(connected=False)
    h = _handlers(ib)
    response = await h.Health(broker_pb2.HealthRequest(), context=object())
    assert response.gateway_connected is False
    assert response.gateway_version == ""


@pytest.mark.asyncio
async def test_health_swallows_isconnected_exception() -> None:
    """A flaky socket must not crash the health check — degrade gracefully."""
    ib = FakeIB(raise_on_connected=True)
    h = _handlers(ib)
    response = await h.Health(broker_pb2.HealthRequest(), context=object())
    assert response.gateway_connected is False
    assert response.gateway_version == ""


@pytest.mark.asyncio
async def test_health_includes_last_tick_timestamp() -> None:
    tick_at = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)
    ib = FakeIB(connected=True)
    h = _handlers(ib, last_tick_ref={"t": tick_at})
    response = await h.Health(broker_pb2.HealthRequest(), context=object())
    assert response.HasField("last_tick_at")
    assert response.last_tick_at.seconds == int(tick_at.timestamp())


@pytest.mark.asyncio
async def test_health_omits_last_tick_when_unset() -> None:
    h = _handlers(FakeIB(connected=True))
    response = await h.Health(broker_pb2.HealthRequest(), context=object())
    assert not response.HasField("last_tick_at")


# ---------- ListManagedAccounts ----------


@pytest.mark.asyncio
async def test_list_managed_accounts_classifies_paper_by_d_prefix() -> None:
    """IBKR paper account numbers begin with 'D' — must surface as MODE_PAPER."""
    ib = FakeIB(
        managed_accounts=["U1234567", "DU2345678"],
        values=[
            FakeAccountValue(account="U1234567", tag="BASE", value="USD"),
            FakeAccountValue(account="DU2345678", tag="BASE", value="GBP"),
        ],
    )
    h = _handlers(ib)
    response = await h.ListManagedAccounts(broker_pb2.Empty(), context=object())
    accounts_by_number = {a.account_number: a for a in response.accounts}
    assert accounts_by_number["U1234567"].mode == broker_pb2.LIVE
    assert accounts_by_number["U1234567"].currency_base == "USD"
    assert accounts_by_number["DU2345678"].mode == broker_pb2.PAPER
    assert accounts_by_number["DU2345678"].currency_base == "GBP"


@pytest.mark.asyncio
async def test_list_managed_accounts_emits_empty_currency_when_base_missing() -> None:
    """Proto contract: currency_base is NOT defaulted when BASE tag is uncached."""
    ib = FakeIB(managed_accounts=["U1234567"], values=[])
    h = _handlers(ib)
    response = await h.ListManagedAccounts(broker_pb2.Empty(), context=object())
    assert response.accounts[0].currency_base == ""


@pytest.mark.asyncio
async def test_list_managed_accounts_propagates_label_as_gateway_label() -> None:
    ib = FakeIB(managed_accounts=["U1"])
    h = _handlers(ib)
    response = await h.ListManagedAccounts(broker_pb2.Empty(), context=object())
    assert response.accounts[0].gateway_label == "ibgw_live_us"


@pytest.mark.asyncio
async def test_list_managed_accounts_returns_empty_when_api_throws() -> None:
    """Don't propagate IBKR API failures into the gRPC stream — surface no accounts."""
    ib = FakeIB(raise_on_managed=True)
    h = _handlers(ib)
    response = await h.ListManagedAccounts(broker_pb2.Empty(), context=object())
    assert list(response.accounts) == []


# ---------- GetAccountSummary ----------


@pytest.mark.asyncio
async def test_get_account_summary_maps_all_5_money_tags() -> None:
    ib = FakeIB(
        values=[
            FakeAccountValue("U1234567", "NetLiquidation", "100000.50", "GBP"),
            FakeAccountValue("U1234567", "TotalCashValue", "25000.00", "GBP"),
            FakeAccountValue("U1234567", "RealizedPnL", "150.25", "GBP"),
            FakeAccountValue("U1234567", "UnrealizedPnL", "-75.10", "GBP"),
            FakeAccountValue("U1234567", "BuyingPower", "200000.00", "GBP"),
        ],
    )
    h = _handlers(ib)
    response = await h.GetAccountSummary(
        broker_pb2.AccountRef(account_number="U1234567"), context=object()
    )
    s = response.summary
    assert s.net_liquidation.value == "100000.50"
    assert s.net_liquidation.currency == "GBP"
    assert s.total_cash.value == "25000.00"
    assert s.realized_pnl.value == "150.25"
    assert s.unrealized_pnl.value == "-75.10"
    assert s.buying_power.value == "200000.00"


@pytest.mark.asyncio
async def test_get_account_summary_filters_to_requested_account() -> None:
    ib = FakeIB(
        values=[
            FakeAccountValue("U1111111", "NetLiquidation", "1.00", "USD"),
            FakeAccountValue("U2222222", "NetLiquidation", "2.00", "USD"),
        ],
    )
    h = _handlers(ib)
    response = await h.GetAccountSummary(
        broker_pb2.AccountRef(account_number="U2222222"), context=object()
    )
    assert response.summary.net_liquidation.value == "2.00"


@pytest.mark.asyncio
async def test_get_account_summary_uses_zero_money_for_missing_tags() -> None:
    """No subscription yet for that tag → Money('0', 'USD') so callers don't NPE."""
    ib = FakeIB(values=[])
    h = _handlers(ib)
    response = await h.GetAccountSummary(
        broker_pb2.AccountRef(account_number="U1234567"), context=object()
    )
    assert response.summary.net_liquidation.value == "0"
    assert response.summary.net_liquidation.currency == "USD"


@pytest.mark.asyncio
async def test_get_account_summary_swallows_unparsable_value() -> None:
    """Garbage in account_values must not crash the handler — fall back to 0."""
    ib = FakeIB(
        values=[FakeAccountValue("U1234567", "NetLiquidation", "not-a-number", "GBP")],
    )
    h = _handlers(ib)
    response = await h.GetAccountSummary(
        broker_pb2.AccountRef(account_number="U1234567"), context=object()
    )
    assert response.summary.net_liquidation.value == "0"


@pytest.mark.asyncio
async def test_get_account_summary_returns_zero_when_api_throws() -> None:
    ib = FakeIB(raise_on_values=True)
    h = _handlers(ib)
    response = await h.GetAccountSummary(
        broker_pb2.AccountRef(account_number="U1234567"), context=object()
    )
    assert response.summary.net_liquidation.value == "0"
