"""OptionChainService — fetches, caches, and routes option chain data."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from typing import Any, cast

import structlog

from app.services import market_calendar

log = structlog.get_logger(__name__)

_CACHE_KEY_FMT = "options:chain:{underlying}:{expiry}:{source}"
_TTL_MARKET_OPEN = 30
_TTL_MARKET_CLOSED = 300

_USD_EXCHANGE = "NYSE"
_HKD_EXCHANGE = "HKEX"

_DEFAULT_BUDGETS: dict[str, int] = {"ibkr": 400, "alpaca": 600, "futu": 400}


class OptionChainService:
    def __init__(self, *, redis: Any, config: Any, broker_registry: Any) -> None:
        self._redis = redis
        self._config = config
        self._broker_registry = broker_registry
        # Singleflight locks: (underlying, expiry_iso, source) → asyncio.Lock
        self._sf_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._sf_lock_meta = asyncio.Lock()
        self._sources: dict[str, list[str]] = {"USD": ["ibkr"], "HKD": ["futu"]}
        self._budgets: dict[str, int] = dict(_DEFAULT_BUDGETS)

    async def reload_config(self) -> None:
        sources = await self._config.get_json("quote_engine", "option_chain_sources", default=None)
        if sources:
            self._sources = sources
        budgets = await self._config.get_json("quote_engine", "option_sub_budgets", default=None)
        if budgets:
            self._budgets = {**_DEFAULT_BUDGETS, **budgets}

    def _cache_key(self, underlying: str, expiry: date, source: str) -> str:
        return _CACHE_KEY_FMT.format(
            underlying=underlying,
            expiry=expiry.isoformat(),
            source=source,
        )

    def _ttl(self, currency: str) -> int:
        exchange = _USD_EXCHANGE if currency == "USD" else _HKD_EXCHANGE
        try:
            is_open = market_calendar.is_open(exchange)
        except Exception:
            is_open = False
        return _TTL_MARKET_OPEN if is_open else _TTL_MARKET_CLOSED

    async def _sf_lock(self, key: tuple[str, str, str]) -> asyncio.Lock:
        async with self._sf_lock_meta:
            if key not in self._sf_locks:
                self._sf_locks[key] = asyncio.Lock()
            return self._sf_locks[key]

    async def get_expirations(self, underlying: str, currency: str) -> list[date]:
        """Return sorted expiry dates for an underlying from the configured primary source."""
        sources = self._sources.get(currency, [])
        for source in sources:
            try:
                result = await self._fetch_expirations_from_source(underlying, currency, source)
                if result:
                    return result
            except Exception as exc:
                log.warning("option_expirations_source_failed", source=source, error=str(exc))
        return []

    async def _fetch_expirations_from_source(
        self, underlying: str, currency: str, source: str
    ) -> list[date]:
        # Sidecar gRPC call implemented when sidecars are extended (Chunk F)
        return []

    async def get_chain(
        self,
        underlying: str,
        expiry: date,
        strike_count: int = 20,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Return option chain. Cache-first; singleflight per (underlying, expiry, source)."""
        sources = self._sources.get(currency, [])
        for source in sources:
            cache_key = self._cache_key(underlying, expiry, source)
            cached = await self._redis.get(cache_key)
            if cached:
                return cast(dict[str, Any], json.loads(cached))

            sf_key = (underlying, expiry.isoformat(), source)
            lock = await self._sf_lock(sf_key)
            async with lock:
                # Double-check after acquiring lock
                cached = await self._redis.get(cache_key)
                if cached:
                    return cast(dict[str, Any], json.loads(cached))
                try:
                    result = await self._fetch_from_sidecar(
                        underlying, expiry, strike_count, source, currency
                    )
                    ttl = self._ttl(currency)
                    await self._redis.setex(cache_key, ttl, json.dumps(result))
                    return result
                except Exception as exc:
                    log.warning("option_chain_source_failed", source=source, error=str(exc))

        return {
            "calls": [],
            "puts": [],
            "source": "none",
            "fetched_at_ms": int(time.time() * 1000),
            "stale": True,
        }

    async def _fetch_from_sidecar(
        self,
        underlying: str,
        expiry: date,
        strike_count: int,
        source: str,
        currency: str,
    ) -> dict[str, Any]:
        """Fetch chain from a specific broker sidecar. Override in tests."""
        raise NotImplementedError(f"Sidecar fetch not yet implemented for {source}")

    async def subscribe_strike_window(
        self,
        underlying_canonical_id: str,
        expiry: date,
        conids: list[str],
    ) -> list[Any]:
        """Subscribe to Greeks streaming for a strike window. Returns SubscriptionHandles."""
        from app.services.options.types import SubscriptionHandle

        return [
            SubscriptionHandle(
                conid=conid,
                canonical_id=None,
                channel=f"greeks.options.{conid}",
            )
            for conid in conids
        ]
