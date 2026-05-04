"""Golden-trace replay tests (Phase 4 Task 18).

Loads JSON fixtures recorded by scripts/record_traces.py against the paper
IB Gateway (Task 17.3, committed in 00d8381) via the _GoldenFakeIB stub in
sidecar/conftest.py and feeds them through BrokerHandlers. Guards against:

1. Proto-shape regressions — handler outputs that no longer round-trip
   through the generated pb2 classes mean the wire contract drifted.
2. Recorder/handler API drift — if a future ib_async upgrade renames a
   method or a field, the assertions surface that immediately.
3. Empty-collection edge cases — paper accounts with no positions/orders/
   fills are the steady state, so the handlers must produce valid empty
   responses, not crash.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from sidecar_ibkr._generated.broker.v1 import broker_pb2
from sidecar_ibkr.handlers import BrokerHandlers


def _make_handlers(ib: object, pnl_cache: object) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        pnl_cache=pnl_cache,  # type: ignore[arg-type]
        label="normal-paper",
        version="test-replay",
        last_tick_ref={"normal-paper": datetime(2026, 4, 25, 17, 0, 0)},
    )


@pytest.mark.asyncio
async def test_health_replay_reports_connected(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)

    resp = await handlers.Health(broker_pb2.HealthRequest(), context=None)

    assert resp.gateway_connected is True
    assert resp.label == "normal-paper"
    assert resp.sidecar_version == "test-replay"


@pytest.mark.asyncio
async def test_listmanagedaccounts_replay_yields_recorded_accounts(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)

    resp = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)

    assert len(resp.accounts) == 6, "fixture recorded six paper accounts"
    numbers = [a.account_number for a in resp.accounts]
    assert all(n.startswith(("DF", "DU")) for n in numbers), (
        "scrubbed paper IDs must keep the D-prefix family the classifier reads"
    )
    # Per IBKR TWS API: BASE is a CURRENCY meta-marker, not a tag. The
    # account base currency is the .currency on the NetLiquidation row.
    # The recorded paper-account fixture ships NetLiquidation rows with
    # currency="GBP" for all six isa-paper accounts (UK base for the real
    # gateways the trace was captured against).
    assert all(a.currency_base == "GBP" for a in resp.accounts)


@pytest.mark.asyncio
async def test_listmanagedaccounts_proto_round_trip(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)
    resp = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)

    raw = resp.SerializeToString()
    decoded = broker_pb2.AccountsResponse.FromString(raw)
    assert decoded == resp


@pytest.mark.asyncio
async def test_getaccountsummary_replay_populates_summary_money_fields(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    """The recorded fixture has 225 account-value rows including
    NetLiquidation, TotalCashValue, and BuyingPower per account. The handler
    must wire those tags into the corresponding Money fields on Summary."""

    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)
    accounts = (
        await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)
    ).accounts
    assert accounts, "fixture must seed at least one account"

    sample = accounts[0].account_number
    resp = await handlers.GetAccountSummary(
        broker_pb2.AccountRef(account_number=sample), context=None
    )

    # SummaryResponse carries a Summary message; every Money field must be
    # present (its `value` is decimal-as-string and may be "0", but the
    # field itself must exist on the proto so the backend can read it).
    assert resp.HasField("summary")
    assert resp.summary.net_liquidation.value != ""
    assert resp.summary.buying_power.value != ""


@pytest.mark.asyncio
async def test_getpositions_replay_empty_paper_account(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)

    resp = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="DUA0000000"), context=None
    )

    assert list(resp.positions) == []


@pytest.mark.asyncio
async def test_getorders_replay_empty_paper_account(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)

    resp = await handlers.GetOrders(
        broker_pb2.AccountRef(account_number="DUA0000000"), context=None
    )

    assert list(resp.orders) == []


@pytest.mark.asyncio
async def test_getcontract_replay_resolves_aapl(
    golden_fake_ib: Callable[[], Any], zero_pnl_cache: Any
) -> None:
    handlers = _make_handlers(golden_fake_ib(), zero_pnl_cache)

    resp = await handlers.GetContract(
        broker_pb2.ContractRef(conid="265598"), context=None
    )

    assert resp.contract.conid == "265598"
    assert resp.contract.symbol == "AAPL"
    assert resp.contract.currency == "USD"
    # Proto Contract carries a single `exchange`; ib_async qualifies AAPL as
    # SMART (the routing exchange) — primary_exchange "NASDAQ" lives on the
    # ib_async side only and isn't on the wire.
    assert resp.contract.exchange == "SMART"
