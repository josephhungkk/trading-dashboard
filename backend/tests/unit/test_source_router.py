"""SourceRouter — config-driven priority + health windowing (HIGH-7)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.core.metrics import QUOTE_ROUTE_CHANGES_TOTAL
from app.models.instruments import AssetClass, Instrument
from app.services.quotes.router import (
    SourceHealthMap,
    SourceHealthState,
    SourceRouter,
)


@pytest.fixture
def config() -> dict[str, Any]:
    return {
        "quote_source_priority": {
            "stock.US": ["schwab", "ibkr", "yfinance"],
            "stock.UK": ["ibkr", "yfinance"],
            "stock.HK": ["futu", "yfinance"],
            "index.US": ["schwab", "ibkr", "yfinance"],
            "warrant.HK": ["futu"],
        },
        "quote_stale_threshold_seconds": {
            "stock.US": 5,
            "stock.UK": 10,
            "stock.HK": 10,
            "index.US": 5,
            "warrant.HK": 10,
        },
    }


@pytest.fixture
def health() -> SourceHealthMap:
    return SourceHealthMap()


def _inst(
    canonical_id: str,
    asset_class: AssetClass = AssetClass.STOCK,
    primary_exchange: str = "NASDAQ",
    currency: str = "USD",
) -> Instrument:
    """Build a minimal Instrument (not session-bound) for routing tests."""
    inst = Instrument(
        canonical_id=canonical_id,
        asset_class=asset_class,
        primary_exchange=primary_exchange,
        currency=currency,
    )
    return inst


# ── route() — primary + fallback ────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_picks_healthy_primary(config: dict[str, Any], health: SourceHealthMap) -> None:
    health.set_state("schwab", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:AAPL:US"))
    assert src == "schwab"


@pytest.mark.asyncio
async def test_route_falls_back_on_primary_down(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    health.set_state("schwab", SourceHealthState.DOWN)
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:AAPL:US"))
    assert src == "ibkr"


@pytest.mark.asyncio
async def test_route_returns_none_when_all_down(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    for s in ("schwab", "ibkr", "yfinance"):
        health.set_state(s, SourceHealthState.DOWN)
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:AAPL:US"))
    assert src is None


@pytest.mark.asyncio
async def test_route_unknown_market_returns_none(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """A canonical_id whose (asset_class, country) has no priority list
    routes to None — engine surfaces NO_INSTRUMENT to FE."""
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:WEIRD:JP"))  # not in config
    assert src is None


@pytest.mark.asyncio
async def test_route_uk_stock(config: dict[str, Any], health: SourceHealthMap) -> None:
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:VOD:UK", primary_exchange="LSE", currency="GBP"))
    assert src == "ibkr"


@pytest.mark.asyncio
async def test_route_hk_warrant(config: dict[str, Any], health: SourceHealthMap) -> None:
    health.set_state("futu", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src = await router.route(
        _inst(
            "warrant:1234:HK",
            asset_class=AssetClass.WARRANT,
            primary_exchange="HKEX",
            currency="HKD",
        )
    )
    assert src == "futu"


# ── compute_health_state() — sliding window ─────────────────────────────


def test_health_window_min_60s_for_quiet_symbols(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """30 s gap on idle warrant -> still HEALTHY (5*10=50 s < 60 s floor)."""
    health.set_state("futu", SourceHealthState.HEALTHY)
    health.update_last_tick("futu", time.monotonic() - 30)
    router = SourceRouter(config, health)
    assert router.compute_health_state("futu", min_threshold=10) == SourceHealthState.HEALTHY


def test_health_window_kicks_in_at_61s(config: dict[str, Any], health: SourceHealthMap) -> None:
    health.set_state("futu", SourceHealthState.HEALTHY)
    health.update_last_tick("futu", time.monotonic() - 61)
    router = SourceRouter(config, health)
    assert router.compute_health_state("futu", min_threshold=10) == SourceHealthState.DEGRADED


def test_health_window_5x_min_threshold_when_above_floor(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """For min_threshold=20, window = max(5*20, 60) = 100 s."""
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    health.update_last_tick("ibkr", time.monotonic() - 90)
    router = SourceRouter(config, health)
    assert router.compute_health_state("ibkr", min_threshold=20) == SourceHealthState.HEALTHY

    health.update_last_tick("ibkr", time.monotonic() - 110)
    assert router.compute_health_state("ibkr", min_threshold=20) == SourceHealthState.DEGRADED


def test_health_state_down_short_circuits(config: dict[str, Any], health: SourceHealthMap) -> None:
    health.set_state("schwab", SourceHealthState.DOWN)
    health.update_last_tick("schwab", time.monotonic())  # very recent
    router = SourceRouter(config, health)
    assert router.compute_health_state("schwab", min_threshold=5) == SourceHealthState.DOWN


def test_health_state_no_ticks_yet_is_degraded(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """A source UP without any ticks → DEGRADED (boot scenario)."""
    health.set_state("yfinance", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    state = router.compute_health_state("yfinance", min_threshold=5)
    assert state == SourceHealthState.DEGRADED


# ── route_with_gateway() — IBKR-specific gateway selection (MED-6) ──────


@pytest.mark.asyncio
async def test_ibkr_gateway_assignment(config: dict[str, Any], health: SourceHealthMap) -> None:
    config["ibkr_gateway_quote_assignment"] = {
        "stock.UK": "isa-live",
        "stock.US": "isa-live",
        "_default": "isa-live",
    }
    config["ibkr_gateway_quote_fallback"] = ["normal-live"]
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    health.set_state("ibkr:isa-live", SourceHealthState.HEALTHY)

    router = SourceRouter(config, health)
    src, gw = await router.route_with_gateway(
        _inst("stock:VOD:UK", primary_exchange="LSE", currency="GBP")
    )
    assert src == "ibkr"
    assert gw == "isa-live"


@pytest.mark.asyncio
async def test_ibkr_gateway_falls_back_when_assigned_down(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    config["ibkr_gateway_quote_assignment"] = {"_default": "isa-live"}
    config["ibkr_gateway_quote_fallback"] = ["normal-live"]
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    health.set_state("ibkr:isa-live", SourceHealthState.DOWN)
    health.set_state("ibkr:normal-live", SourceHealthState.HEALTHY)

    router = SourceRouter(config, health)
    src, gw = await router.route_with_gateway(
        _inst("stock:VOD:UK", primary_exchange="LSE", currency="GBP")
    )
    assert src == "ibkr"
    assert gw == "normal-live"


@pytest.mark.asyncio
async def test_route_with_gateway_no_gateway_for_non_ibkr(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    health.set_state("schwab", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    src, gw = await router.route_with_gateway(_inst("stock:AAPL:US"))
    assert src == "schwab"
    assert gw is None


# ── reroute() + metric ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reroute_fires_route_change_metric(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """When the primary fails, reroute() picks the next healthy source AND
    bumps quote_route_changes_total{from,to,asset_class}."""
    health.set_state("schwab", SourceHealthState.DOWN)
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)

    before = QUOTE_ROUTE_CHANGES_TOTAL.labels(
        from_source="schwab", to_source="ibkr", asset_class="stock"
    )._value.get()

    new_src = await router.reroute(_inst("stock:AAPL:US"), current="schwab", reason="source_down")

    assert new_src == "ibkr"
    after = QUOTE_ROUTE_CHANGES_TOTAL.labels(
        from_source="schwab", to_source="ibkr", asset_class="stock"
    )._value.get()
    assert after - before == 1


@pytest.mark.asyncio
async def test_reroute_returns_none_when_no_alternative(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    for s in ("schwab", "ibkr", "yfinance"):
        health.set_state(s, SourceHealthState.DOWN)
    router = SourceRouter(config, health)
    new_src = await router.reroute(_inst("stock:AAPL:US"), current="schwab", reason="all_down")
    assert new_src is None


# ── _derive_country fallback (defence-in-depth shim) ────────────────────


@pytest.mark.asyncio
async def test_derive_country_fallback_uses_exchange_when_canonical_malformed(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """If a malformed canonical_id slips through (post-A4 invariant should
    prevent this), router falls back to country_for_exchange."""
    health.set_state("ibkr", SourceHealthState.HEALTHY)
    router = SourceRouter(config, health)
    # Two-segment canonical_id is malformed → ValueError → exchange fallback.
    inst = _inst("BROKEN:AAPL", primary_exchange="LSE", currency="GBP")
    src = await router.route(inst)
    assert src == "ibkr"  # stock.UK priority list, derived from LSE→UK


@pytest.mark.asyncio
async def test_derive_country_returns_none_when_both_paths_fail(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """Malformed canonical_id AND unknown exchange → routing returns None."""
    router = SourceRouter(config, health)
    inst = _inst("BROKEN:AAPL", primary_exchange="MADEUP", currency="USD")
    src = await router.route(inst)
    assert src is None


# ── unknown source fail-closed (HIGH fix) ───────────────────────────────


@pytest.mark.asyncio
async def test_unknown_source_in_priority_is_skipped(
    config: dict[str, Any], health: SourceHealthMap
) -> None:
    """Typo'd source id in quote_source_priority must NOT route — fails
    closed because the source was never registered via set_state."""
    config["quote_source_priority"]["stock.US"] = ["schwab_typo", "schwab"]
    health.set_state("schwab", SourceHealthState.HEALTHY)
    # Note: schwab_typo is NEVER registered.
    router = SourceRouter(config, health)
    src = await router.route(_inst("stock:AAPL:US"))
    assert src == "schwab"  # skips schwab_typo (unknown == fail-closed)


# ── set_state side effect on metric ──────────────────────────────────────


def test_set_state_updates_health_gauge(health: SourceHealthMap) -> None:
    """SourceHealthMap.set_state must reflect into quote_source_health_state."""
    from app.core.metrics import QUOTE_SOURCE_HEALTH_STATE

    health.set_state("schwab", SourceHealthState.HEALTHY)
    assert QUOTE_SOURCE_HEALTH_STATE.labels(source="schwab")._value.get() == 2

    health.set_state("schwab", SourceHealthState.DEGRADED)
    assert QUOTE_SOURCE_HEALTH_STATE.labels(source="schwab")._value.get() == 1

    health.set_state("schwab", SourceHealthState.DOWN)
    assert QUOTE_SOURCE_HEALTH_STATE.labels(source="schwab")._value.get() == 0
