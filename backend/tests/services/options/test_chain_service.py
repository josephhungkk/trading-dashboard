"""Tests for OptionChainService — cache, singleflight, source routing, exchange-aware TTL."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_service(*, redis=None, config_values=None, sidecar=None):
    from app.services.options.chain_service import OptionChainService

    redis = redis or AsyncMock()
    config_values = config_values or {
        "quote_engine/option_chain_sources": {"USD": ["ibkr"], "HKD": ["futu"]}
    }

    async def get_json(ns, key, default=None):
        return config_values.get(f"{ns}/{key}", default)

    cfg = MagicMock()
    cfg.get_json = get_json

    svc = OptionChainService(redis=redis, config=cfg, broker_registry=MagicMock())
    if sidecar is not None:
        svc._sidecar = sidecar
    return svc


@pytest.mark.asyncio
async def test_get_chain_cache_hit() -> None:
    """Cache hit should return without calling sidecar."""
    redis = AsyncMock()
    cached_row = {
        "conid": "123",
        "strike": "450.00",
        "put_call": "C",
        "bid": "5.00",
        "ask": "5.20",
        "iv": 0.175,
        "delta": 0.5,
        "gamma": 0.028,
        "theta": -0.12,
        "vega": 0.31,
        "open_interest": 38000,
        "volume": 12000,
        "multiplier": 100,
        "exchange": "CBOE",
        "style": "A",
    }
    cached = {
        "calls": [cached_row],
        "puts": [],
        "source": "ibkr",
        "fetched_at_ms": 1700000000000,
    }
    redis.get = AsyncMock(return_value=json.dumps(cached))

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock()

    result = await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    svc._fetch_from_sidecar.assert_not_called()
    assert result["source"] == "ibkr"


@pytest.mark.asyncio
async def test_get_chain_cache_miss_calls_sidecar() -> None:
    """Cache miss should call sidecar and populate cache."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    fake_response = {"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 1700000000000}
    svc._fetch_from_sidecar = AsyncMock(return_value=fake_response)

    result = await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    svc._fetch_from_sidecar.assert_called_once()
    redis.setex.assert_called_once()
    assert result["source"] == "ibkr"


@pytest.mark.asyncio
async def test_exchange_aware_ttl_market_open() -> None:
    """During market hours, TTL should be 30s."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock(
        return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0}
    )

    with patch("app.services.options.chain_service.market_calendar") as mc:
        mc.is_open.return_value = True
        await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    call_args = redis.setex.call_args
    assert call_args[0][1] == 30


@pytest.mark.asyncio
async def test_exchange_aware_ttl_market_closed() -> None:
    """Outside market hours, TTL should be 300s."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock(
        return_value={"calls": [], "puts": [], "source": "ibkr", "fetched_at_ms": 0}
    )

    with patch("app.services.options.chain_service.market_calendar") as mc:
        mc.is_open.return_value = False
        await svc.get_chain("SPY", date(2025, 1, 17), strike_count=20, currency="USD")

    call_args = redis.setex.call_args
    assert call_args[0][1] == 300


@pytest.mark.asyncio
async def test_all_sources_fail_returns_stale() -> None:
    """When all sources fail, returns stale payload rather than raising."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    svc = _make_service(redis=redis)
    svc._fetch_from_sidecar = AsyncMock(side_effect=RuntimeError("sidecar down"))

    result = await svc.get_chain("SPY", date(2025, 1, 17), currency="USD")

    assert result["stale"] is True
    assert result["calls"] == []


@pytest.mark.asyncio
async def test_reload_config_updates_sources() -> None:
    config = AsyncMock()
    config.get_json = AsyncMock(side_effect=[{"USD": ["schwab"]}, None])
    from app.services.options.chain_service import OptionChainService

    svc = OptionChainService(config=config, redis=AsyncMock(), broker_registry=AsyncMock())
    await svc.reload_config()
    assert svc._sources == {"USD": ["schwab"]}
