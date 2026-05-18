"""ContractResolver — GetFutureContracts RPC wrapper with Redis singleflight cache."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date
from decimal import Decimal
from typing import Any

import structlog

from app.services import market_calendar
from app.services.futures.types import FutureContractMonth

log = structlog.get_logger(__name__)

_CACHE_KEY_FMT = "futures:contracts:{broker}:{root_symbol}"
_TTL_MARKET_OPEN = 300
_TTL_MARKET_CLOSED = 3600
_MAX_MONTHS = 6


class ContractResolver:
    def __init__(self, *, redis: Any, config: Any, broker_registry: Any) -> None:
        self._redis = redis
        self._config = config
        self._broker_registry = broker_registry
        self._sf_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._sf_lock_meta = asyncio.Lock()

    async def _sf_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        async with self._sf_lock_meta:
            if key not in self._sf_locks:
                self._sf_locks[key] = asyncio.Lock()
            return self._sf_locks[key]

    def _ttl(self) -> int:
        try:
            from datetime import datetime

            is_open = market_calendar.is_open("CME", datetime.now(UTC))
        except Exception:
            is_open = False
        return _TTL_MARKET_OPEN if is_open else _TTL_MARKET_CLOSED

    def _cache_key(self, broker: str, root_symbol: str) -> str:
        return _CACHE_KEY_FMT.format(broker=broker, root_symbol=root_symbol)

    async def get_contracts(self, root_symbol: str, *, broker: str) -> list[FutureContractMonth]:
        from app.core import metrics

        cache_key = self._cache_key(broker, root_symbol)
        cached = await self._redis.get(cache_key)
        if cached:
            metrics.FUTURES_CONTRACT_RESOLVER_CACHE_HITS_TOTAL.labels(root_symbol=root_symbol).inc()
            data = json.loads(cached)
            return [FutureContractMonth.from_cache_dict(d, root_symbol) for d in data]

        sf_key = (broker, root_symbol)
        lock = await self._sf_lock(sf_key)
        async with lock:
            cached = await self._redis.get(cache_key)
            if cached:
                metrics.FUTURES_CONTRACT_RESOLVER_CACHE_HITS_TOTAL.labels(
                    root_symbol=root_symbol
                ).inc()
                data = json.loads(cached)
                return [FutureContractMonth.from_cache_dict(d, root_symbol) for d in data]
            try:
                contracts = await self._fetch_from_sidecar(root_symbol, broker)
                payload = json.dumps([c.to_cache_dict() for c in contracts])
                await self._redis.setex(cache_key, self._ttl(), payload)
                return contracts
            except Exception as exc:
                log.warning(
                    "contract_resolver_fetch_failed",
                    broker=broker,
                    symbol=root_symbol,
                    error=str(exc),
                )
                return []

    async def _fetch_from_sidecar(self, root_symbol: str, broker: str) -> list[FutureContractMonth]:
        from app._generated.broker.v1 import broker_pb2
        from app.core import metrics

        stub = self._broker_registry
        request = broker_pb2.GetFutureContractsRequest(root_symbol=root_symbol, broker_id=broker)
        try:
            response = await stub.GetFutureContracts(request)
            metrics.FUTURES_CONTRACT_RESOLVER_FETCH_TOTAL.labels(
                root_symbol=root_symbol, outcome="ok"
            ).inc()
        except Exception:
            metrics.FUTURES_CONTRACT_RESOLVER_FETCH_TOTAL.labels(
                root_symbol=root_symbol, outcome="error"
            ).inc()
            raise

        contracts: list[FutureContractMonth] = []
        for m in response.contracts[:_MAX_MONTHS]:
            first_notice_day: date | None = None
            first_notice_raw = getattr(m, "first_notice", "") or ""
            if first_notice_raw:
                try:
                    first_notice_day = date.fromisoformat(first_notice_raw)
                except ValueError:
                    pass
            expiry_raw = getattr(m, "expiry_date", "") or ""
            try:
                expiry = date.fromisoformat(expiry_raw) if expiry_raw else date.today()
            except ValueError:
                expiry = date.today()
            contracts.append(
                FutureContractMonth(
                    conid=m.conid,
                    contract_month=m.contract_month,
                    expiry=expiry,
                    exchange=getattr(m, "exchange", ""),
                    multiplier=Decimal(m.multiplier or "1"),
                    tick_size=Decimal(m.tick_size or "0.01"),
                    tick_value=Decimal(m.tick_value or "0"),
                    settlement_type=m.settlement_type
                    if m.settlement_type in ("CASH", "PHYSICAL")
                    else "CASH",
                    first_notice_day=first_notice_day,
                    underlying_symbol=root_symbol,
                )
            )
        return contracts
