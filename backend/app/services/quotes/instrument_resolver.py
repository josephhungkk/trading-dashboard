"""InstrumentResolver — race-safe upsert for ``instruments`` + ``symbol_aliases``.

Phase 7b.1 CRIT-3 mitigation. Two-layer guard:

* **In-process** — ``dict[canonical_id, asyncio.Lock]`` (lazy-created, capped at
  5000 entries, TTL 1h since last access). Same-symbol concurrent callers
  serialize through one lock so the second waiter sees the post-INSERT row.
* **DB layer** — ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` for both
  tables, with a ``SELECT`` fallback when ``RETURNING`` is empty. Cross-process
  safety relies on the unique index on ``instruments.canonical_id`` and the
  composite PK on ``symbol_aliases(source, raw_symbol)``.

The resolver does **not** commit — the caller owns the transaction. Production
QuoteEngine commits per cycle; tests roll back via ``db_session.rollback()``.
This avoids clashing with autobegin'd sessions and with outer-transaction test
fixtures (memory ``feedback_pytest_session_begin_commits.md``).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    QUOTE_ALIASES_CREATED_TOTAL,
    QUOTE_INSTRUMENTS_CREATED_TOTAL,
)
from app.models.instruments import AssetClass, Instrument, SymbolAlias

LOCK_CACHE_MAX = 5000
LOCK_CACHE_TTL_SECONDS = 3600


class InstrumentResolver:
    """Resolve or create an :class:`Instrument` and its :class:`SymbolAlias`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._locks: OrderedDict[str, tuple[asyncio.Lock, float]] = OrderedDict()
        self._locks_guard = asyncio.Lock()

    async def _get_lock(self, canonical_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            now = time.monotonic()
            entry = self._locks.get(canonical_id)
            if entry is not None:
                lock, _ = entry
                self._locks[canonical_id] = (lock, now)
                self._locks.move_to_end(canonical_id)
                return lock

            lock = asyncio.Lock()
            self._locks[canonical_id] = (lock, now)
            self._locks.move_to_end(canonical_id)
            self._evict_locked(now)
            return lock

    def _evict_locked(self, now: float) -> None:
        ttl_cutoff = now - LOCK_CACHE_TTL_SECONDS
        stale = [k for k, (_, ts) in self._locks.items() if ts < ttl_cutoff]
        for key in stale:
            self._locks.pop(key, None)

        while len(self._locks) > LOCK_CACHE_MAX:
            self._locks.popitem(last=False)

    async def resolve_or_create(
        self,
        *,
        canonical_id: str,
        source: str,
        raw_symbol: str,
        asset_class: AssetClass,
        primary_exchange: str,
        currency: str,
        meta: dict[str, Any] | None = None,
        alias_meta: dict[str, Any] | None = None,
    ) -> Instrument:
        """Return the :class:`Instrument` for ``canonical_id``, creating
        instrument + alias rows on first observation. Idempotent.
        """
        lock = await self._get_lock(canonical_id)
        async with lock:
            instrument = await self._upsert_instrument(
                canonical_id=canonical_id,
                asset_class=asset_class,
                primary_exchange=primary_exchange,
                currency=currency,
                meta=meta or {},
            )
            await self._upsert_alias(
                source=source,
                raw_symbol=raw_symbol,
                instrument_id=instrument.id,
                meta=alias_meta or {},
            )
            return instrument

    async def _upsert_instrument(
        self,
        *,
        canonical_id: str,
        asset_class: AssetClass,
        primary_exchange: str,
        currency: str,
        meta: dict[str, Any],
    ) -> Instrument:
        values: dict[str, Any] = {
            "canonical_id": canonical_id,
            "asset_class": asset_class,
            "primary_exchange": primary_exchange,
            "currency": currency,
            "meta": meta,
        }
        display_name = meta.get("display_name") if meta else None
        if isinstance(display_name, str):
            values["display_name"] = display_name

        stmt = (
            pg_insert(Instrument)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["canonical_id"])
            .returning(Instrument.id)
        )
        result = await self._session.execute(stmt)
        new_id = result.scalar_one_or_none()

        if new_id is not None:
            QUOTE_INSTRUMENTS_CREATED_TOTAL.labels(asset_class=asset_class.value).inc()
            inst = await self._session.get(Instrument, new_id)
            assert inst is not None
            return inst

        existing = await self._session.execute(
            select(Instrument).where(Instrument.canonical_id == canonical_id)
        )
        return existing.scalar_one()

    async def _upsert_alias(
        self,
        *,
        source: str,
        raw_symbol: str,
        instrument_id: int,
        meta: dict[str, Any],
    ) -> None:
        stmt = (
            pg_insert(SymbolAlias)
            .values(
                source=source,
                raw_symbol=raw_symbol,
                instrument_id=instrument_id,
                meta=meta,
            )
            .on_conflict_do_nothing(index_elements=["source", "raw_symbol"])
            .returning(SymbolAlias.source)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            QUOTE_ALIASES_CREATED_TOTAL.labels(source=source).inc()

    async def list_aliases(self, instrument_id: int) -> list[SymbolAlias]:
        result = await self._session.execute(
            select(SymbolAlias).where(SymbolAlias.instrument_id == instrument_id)
        )
        return list(result.scalars().all())

    async def from_legacy(
        self,
        broker_id: str,
        raw_symbol: str,
        exchange: str,
        currency: str,
    ) -> Instrument | None:
        """Best-effort canonical_id derivation for ``instruments_seed`` (MED-8).

        Returns ``None`` when the inputs cannot be mapped — caller logs
        ``quote_seed_skipped_total{reason}`` and continues. Never raises.
        """
        country = _country_for_exchange(exchange)
        if country is None:
            return None
        canonical_id = f"stock:{raw_symbol}:{country}"
        try:
            return await self.resolve_or_create(
                canonical_id=canonical_id,
                source=broker_id,
                raw_symbol=raw_symbol,
                asset_class=AssetClass.STOCK,
                primary_exchange=exchange,
                currency=currency,
                alias_meta={"exchange": exchange, "sec_type": "STK"},
            )
        except Exception:
            return None


_EXCHANGE_TO_COUNTRY: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "ARCA": "US",
    "AMEX": "US",
    "BATS": "US",
    "CBOE": "US",
    "LSE": "UK",
    "LSEETF": "UK",
    "SEHK": "HK",
    "HKEX": "HK",
    "TSE": "JP",
    "TSEJ": "JP",
}


def _country_for_exchange(exchange: str) -> str | None:
    if not exchange:
        return None
    return _EXCHANGE_TO_COUNTRY.get(exchange.upper())
