"""SourceRouter integration test — Phase 7c MED-1 routing coverage.

Exercises the SourceRouter against the v0.7.3 default routing table:

1. Happy path — crypto:BTC:US → alpaca; stock.US → schwab.
2. Schwab DOWN → stock.US reroutes to alpaca (per-key fallback).
3. Both schwab + alpaca DOWN for stock.US → falls through to ibkr.
4. crypto.US + alpaca DOWN → no fallback yet (Phase 7b.2 coinbase).
"""

from __future__ import annotations

import pytest

from app.models.instruments import AssetClass, Instrument
from app.services.quotes.router import (
    SourceHealthMap,
    SourceHealthState,
    SourceRouter,
)


def _instrument(asset_class: AssetClass, canonical_id: str) -> Instrument:
    return Instrument(
        canonical_id=canonical_id,
        asset_class=asset_class,
        primary_exchange="",
        currency="USD",
        meta={},
    )


@pytest.fixture
def healthy_router() -> SourceRouter:
    """All sources HEALTHY — defaults from config_defaults.py apply."""
    health = SourceHealthMap()
    for src in ("schwab", "alpaca", "ibkr", "futu", "yfinance"):
        health.set_state(src, SourceHealthState.HEALTHY)
    return SourceRouter({}, health)


@pytest.mark.asyncio
async def test_happy_path_crypto_to_alpaca_stock_to_schwab(
    healthy_router: SourceRouter,
) -> None:
    """crypto:BTC:US routes to alpaca primary; stock:AAPL:US routes to schwab."""
    btc = _instrument(AssetClass.CRYPTO, "crypto:BTC:US")
    aapl = _instrument(AssetClass.STOCK, "stock:AAPL:US")
    assert await healthy_router.route(btc) == "alpaca"
    assert await healthy_router.route(aapl) == "schwab"


@pytest.mark.asyncio
async def test_schwab_down_reroutes_stock_us_to_alpaca() -> None:
    """When schwab is DOWN, stock.US falls through to alpaca (next in list)."""
    health = SourceHealthMap()
    for src in ("alpaca", "ibkr"):
        health.set_state(src, SourceHealthState.HEALTHY)
    health.set_state("schwab", SourceHealthState.DOWN)
    router = SourceRouter({}, health)
    aapl = _instrument(AssetClass.STOCK, "stock:AAPL:US")
    assert await router.route(aapl) == "alpaca"


@pytest.mark.asyncio
async def test_schwab_and_alpaca_down_falls_through_to_ibkr() -> None:
    """Two-DOWN scenario: schwab + alpaca both DOWN → ibkr (last in list)."""
    health = SourceHealthMap()
    health.set_state("schwab", SourceHealthState.DOWN)
    health.set_state("alpaca", SourceHealthState.DOWN)
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter({}, health)
    aapl = _instrument(AssetClass.STOCK, "stock:AAPL:US")
    assert await router.route(aapl) == "ibkr"


@pytest.mark.asyncio
async def test_crypto_us_alpaca_down_no_fallback_until_7b2() -> None:
    """crypto.US has only alpaca in v0.7.3 — alpaca DOWN → None.

    Phase 7b.2 will append "coinbase" to crypto.US fallback list.
    """
    health = SourceHealthMap()
    health.set_state("alpaca", SourceHealthState.DOWN)
    router = SourceRouter({}, health)
    btc = _instrument(AssetClass.CRYPTO, "crypto:BTC:US")
    assert await router.route(btc) is None
