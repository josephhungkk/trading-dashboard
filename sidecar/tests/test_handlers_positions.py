"""Tests for sidecar.handlers GetPositions (Task 12).

Anchors the four critical invariants of the positions path:
  1. UK-pence GBX scaling on market_price (LSE/LSEETF/BATEUK/CHIXUK).
  2. Multi-account isolation (cross-account rows must be filtered + WARN'd).
  3. PnL flows through PnLCache.snapshot — never naive math (per stock_splits.md).
  4. IBKR API failure must NOT propagate; degrade to empty PositionsResponse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
import structlog.testing

from sidecar._generated.broker.v1 import broker_pb2
from sidecar.handlers import BrokerHandlers


@dataclass
class FakeContract:
    """Mirrors ib_async.Contract's read surface."""

    conId: int  # noqa: N815 — match ib_async API
    symbol: str
    exchange: str
    currency: str
    secType: str = "STK"  # noqa: N815
    localSymbol: str = ""  # noqa: N815


@dataclass
class FakePosition:
    """Mirrors ib_async.Position's read surface."""

    account: str
    contract: FakeContract
    position: Decimal
    avgCost: Decimal  # noqa: N815
    marketPrice: Decimal  # noqa: N815


@dataclass
class FakeIB:
    """Minimal ib_async.IB stand-in for GetPositions tests."""

    positions: list[FakePosition] = field(default_factory=list)
    raise_on_positions: bool = False

    async def reqPositionsAsync(self) -> list[FakePosition]:  # noqa: N802
        if self.raise_on_positions:
            raise RuntimeError("api timeout")
        return list(self.positions)


class StubPnLCache:
    """In-memory PnL stub keyed by (account, conid).

    Bypasses ib_async.reqPnLSingleAsync entirely so tests don't have to
    fake the full PnLCache.get path — handlers.GetPositions only ever
    calls .snapshot(), never .get().
    """

    def __init__(
        self,
        snapshots: dict[
            tuple[str, int],
            tuple[Decimal | None, Decimal | None, Decimal | None],
        ],
    ) -> None:
        self._snapshots = snapshots

    def snapshot(
        self, account: str, conid: int
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        return self._snapshots.get((account, conid), (None, None, None))


def _handlers(
    ib: FakeIB,
    pnl_snapshots: dict[
        tuple[str, int],
        tuple[Decimal | None, Decimal | None, Decimal | None],
    ]
    | None = None,
) -> BrokerHandlers:
    return BrokerHandlers(
        ib=ib,  # type: ignore[arg-type]
        pnl_cache=StubPnLCache(pnl_snapshots or {}),  # type: ignore[arg-type]
        label="ibgw_live_us",
        version="0.4.0+test",
        last_tick_ref={},
    )


# ---------- account filter ----------


@pytest.mark.asyncio
async def test_get_positions_filters_to_requested_account() -> None:
    """Multi-account ib_async.reqPositionsAsync output must be account-scoped."""
    aapl = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    msft = FakeContract(conId=272093, symbol="MSFT", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180.5"), Decimal("190.0")),
            FakePosition("U2222222", msft, Decimal("5"), Decimal("400.0"), Decimal("420.0")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert len(response.positions) == 1
    assert response.positions[0].contract.symbol == "AAPL"


@pytest.mark.asyncio
async def test_get_positions_emits_warn_when_rows_dropped() -> None:
    """CRITICAL invariant: multi-account leakage must be loud.

    Uses structlog.testing.capture_logs since handlers.py emits via structlog;
    pytest's caplog only sees stdlib records and we don't bind structlog to
    the stdlib logger inside the test process.
    """
    aapl = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    msft = FakeContract(conId=272093, symbol="MSFT", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180"), Decimal("190")),
            FakePosition("U2222222", msft, Decimal("5"), Decimal("400"), Decimal("420")),
            FakePosition("U3333333", msft, Decimal("3"), Decimal("400"), Decimal("420")),
        ],
    )
    h = _handlers(ib)
    with structlog.testing.capture_logs() as captured:
        await h.GetPositions(
            broker_pb2.AccountRef(account_number="U1111111"), context=object()
        )
    matching = [
        e for e in captured
        if e.get("event") == "ibkr_positions_filtered_rows"
        and e.get("log_level") == "warning"
    ]
    assert matching, "expected WARN ibkr_positions_filtered_rows when cross-account rows dropped"
    assert matching[0]["account_number"] == "U1111111"
    assert matching[0]["dropped_rows"] == 2


@pytest.mark.asyncio
async def test_get_positions_does_not_warn_when_no_filter() -> None:
    """Single-account result must not emit the dropped_rows WARN."""
    aapl = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180"), Decimal("190")),
        ],
    )
    h = _handlers(ib)
    with structlog.testing.capture_logs() as captured:
        response = await h.GetPositions(
            broker_pb2.AccountRef(account_number="U1111111"), context=object()
        )
    assert len(response.positions) == 1
    assert not any(e.get("event") == "ibkr_positions_filtered_rows" for e in captured)


# ---------- GBX (UK pence) normalization ----------


@pytest.mark.asyncio
async def test_get_positions_scales_lse_market_price_from_pence_to_pounds() -> None:
    """LSE GBP quotes arrive in pence — sidecar must divide by 100 before emitting."""
    sgln = FakeContract(conId=88888, symbol="SGLN", exchange="LSE", currency="GBP", secType="ETF")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", sgln, Decimal("100"), Decimal("0"), Decimal("12000")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert response.positions[0].market_price.value == "120"
    assert response.positions[0].market_price.currency == "GBP"
    # market_value MUST also be in normalized pounds, not pence.
    assert response.positions[0].market_value.value == "12000"  # 100 shares * GBP 120


@pytest.mark.asyncio
async def test_get_positions_does_not_scale_us_quote() -> None:
    """USD-denominated NASDAQ positions must pass through untouched."""
    aapl = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180.5"), Decimal("190.25")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert response.positions[0].market_price.value == "190.25"


@pytest.mark.asyncio
async def test_get_positions_does_not_scale_ibis_eur_quote() -> None:
    """CR-1 regression guard: IBIS is Frankfurt EUR — never /100, even if currency=GBP."""
    sap = FakeContract(conId=33333, symbol="SAP", exchange="IBIS", currency="EUR")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", sap, Decimal("10"), Decimal("125"), Decimal("130")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert response.positions[0].market_price.value == "130"
    assert response.positions[0].market_price.currency == "EUR"


# ---------- PnL via cache (split-aware path per stock_splits.md) ----------


@pytest.mark.asyncio
async def test_get_positions_reads_pnl_from_cache_not_naive_math() -> None:
    """stock_splits.md: avg_cost*qty math fakes -98% losses after splits.

    All three PnL fields must come from PnLCache.snapshot, never from
    market_value - (avgCost * position).
    """
    vwrp = FakeContract(conId=44444, symbol="VWRP", exchange="LSE", currency="GBP", secType="ETF")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", vwrp, Decimal("100"), Decimal("9000"), Decimal("12000")),
        ],
    )
    snapshots = {
        ("U1111111", 44444): (Decimal("450.00"), Decimal("12.50"), Decimal("3.75")),
    }
    h = _handlers(ib, pnl_snapshots=snapshots)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    p = response.positions[0]
    assert p.unrealized_pnl.value == "450.00"
    assert p.realized_pnl_today.value == "12.50"
    assert p.daily_pnl.value == "3.75"


@pytest.mark.asyncio
async def test_get_positions_zero_money_when_pnl_cache_empty() -> None:
    """During the 30s warm-up window snapshot returns (None, None, None) — emit 0."""
    aapl = FakeContract(conId=265598, symbol="AAPL", exchange="NASDAQ", currency="USD")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180"), Decimal("190")),
        ],
    )
    h = _handlers(ib)  # no snapshots
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    p = response.positions[0]
    assert p.unrealized_pnl.value == "0"
    assert p.realized_pnl_today.value == "0"
    assert p.daily_pnl.value == "0"


# ---------- contract field passthrough ----------


@pytest.mark.asyncio
async def test_get_positions_maps_contract_fields() -> None:
    aapl = FakeContract(
        conId=265598,
        symbol="AAPL",
        exchange="NASDAQ",
        currency="USD",
        secType="STK",
        localSymbol="AAPL",
    )
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", aapl, Decimal("10"), Decimal("180"), Decimal("190")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    c = response.positions[0].contract
    assert c.symbol == "AAPL"
    assert c.exchange == "NASDAQ"
    assert c.currency == "USD"
    assert c.conid == "265598"
    assert c.asset_class == broker_pb2.STOCK
    assert c.local_symbol == "AAPL"


@pytest.mark.asyncio
async def test_get_positions_maps_etf_sectype() -> None:
    """ib_async secType 'ETF' must map to proto AssetClass.ETF."""
    sgln = FakeContract(conId=88888, symbol="SGLN", exchange="LSE", currency="GBP", secType="ETF")
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", sgln, Decimal("100"), Decimal("0"), Decimal("12000")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert response.positions[0].contract.asset_class == broker_pb2.ETF


@pytest.mark.asyncio
async def test_get_positions_unknown_sectype_falls_back_to_unspecified() -> None:
    """Unmapped IBKR secType strings must not crash — fall back to ASSET_UNSPECIFIED."""
    weird = FakeContract(
        conId=99999, symbol="WEIRD", exchange="SMART", currency="USD", secType="ZZZ"
    )
    ib = FakeIB(
        positions=[
            FakePosition("U1111111", weird, Decimal("1"), Decimal("1"), Decimal("1")),
        ],
    )
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert response.positions[0].contract.asset_class == broker_pb2.ASSET_UNSPECIFIED


# ---------- error path ----------


@pytest.mark.asyncio
async def test_get_positions_returns_empty_when_api_throws() -> None:
    """ib_async.reqPositionsAsync exception must surface as empty list, not crash."""
    ib = FakeIB(raise_on_positions=True)
    h = _handlers(ib)
    response = await h.GetPositions(
        broker_pb2.AccountRef(account_number="U1111111"), context=object()
    )
    assert list(response.positions) == []
