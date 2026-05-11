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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    QUOTE_ALIASES_CREATED_TOTAL,
    QUOTE_INSTRUMENTS_CREATED_TOTAL,
)
from app.models.instruments import AssetClass, Instrument, SymbolAlias
from app.services.quotes.base import country_for_exchange as _country_for_exchange

LOCK_CACHE_MAX = 5000
LOCK_CACHE_TTL_SECONDS = 3600

# JSONB-friendly value shape — keeps mypy strict without leaking ``Any`` into
# public signatures. Nested dict/list allowed for asset-class-specific extensions
# (option strikes, ISIN, etc.) per spec §4.1.
MetaScalar = str | int | float | bool | None
MetaDict = dict[str, "MetaScalar | list[MetaScalar] | dict[str, MetaScalar]"]

_log = structlog.get_logger(__name__)


@dataclass
class _LockEntry:
    """Per-canonical_id lock + last-access timestamp + in-flight ref count.

    ``refcount`` is incremented while a coroutine is inside ``async with lock``
    and decremented on exit. The eviction policy refuses to remove entries with
    ``refcount > 0`` so a long-running holder is never disconnected from new
    waiters for the same canonical_id.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = 0.0
    refcount: int = 0


class InstrumentResolver:
    """Resolve or create an :class:`Instrument` and its :class:`SymbolAlias`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._locks: OrderedDict[str, _LockEntry] = OrderedDict()
        self._locks_guard = asyncio.Lock()

    async def _get_or_create_entry(self, canonical_id: str) -> _LockEntry:
        async with self._locks_guard:
            now = time.monotonic()
            entry = self._locks.get(canonical_id)
            if entry is None:
                entry = _LockEntry(last_access=now)
                self._locks[canonical_id] = entry
            else:
                entry.last_access = now

            self._locks.move_to_end(canonical_id)
            entry.refcount += 1
            self._evict_locked(now)
            return entry

    async def _release_entry(self, canonical_id: str) -> None:
        async with self._locks_guard:
            entry = self._locks.get(canonical_id)
            if entry is not None:
                entry.refcount = max(0, entry.refcount - 1)

    @asynccontextmanager
    async def _symbol_lock(self, canonical_id: str) -> AsyncIterator[None]:
        entry = await self._get_or_create_entry(canonical_id)
        try:
            async with entry.lock:
                yield
        finally:
            await self._release_entry(canonical_id)

    def _evict_locked(self, now: float) -> None:
        """Caller must hold ``self._locks_guard``. Never evicts entries with
        ``refcount > 0`` — a held lock must not be disconnected from waiters."""
        ttl_cutoff = now - LOCK_CACHE_TTL_SECONDS
        for key in [
            k for k, e in self._locks.items() if e.last_access < ttl_cutoff and e.refcount == 0
        ]:
            self._locks.pop(key, None)

        if len(self._locks) <= LOCK_CACHE_MAX:
            return
        for key in list(self._locks.keys()):
            if len(self._locks) <= LOCK_CACHE_MAX:
                break
            entry = self._locks[key]
            if entry.refcount == 0:
                self._locks.pop(key, None)

    async def resolve_or_create(
        self,
        *,
        canonical_id: str,
        source: str,
        raw_symbol: str,
        asset_class: AssetClass,
        primary_exchange: str,
        currency: str,
        meta: MetaDict | None = None,
        alias_meta: MetaDict | None = None,
    ) -> Instrument:
        """Return the :class:`Instrument` for ``canonical_id``, creating
        instrument + alias rows on first observation. Idempotent.
        """
        async with self._symbol_lock(canonical_id):
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
        meta: MetaDict,
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
            if inst is None:  # pragma: no cover — invariant violation, not assert
                raise LookupError(
                    f"INSERT RETURNING id={new_id} but session.get returned None "
                    f"for canonical_id={canonical_id!r}"
                )
            return inst

        # Conflict path: ON CONFLICT DO NOTHING blocked until the writer that
        # owns the existing row committed (PG row-lock semantics), so the SELECT
        # in this transaction is guaranteed to see it.
        existing = await self._session.execute(
            select(Instrument).where(Instrument.canonical_id == canonical_id)
        )
        inst = existing.scalar_one_or_none()
        if inst is None:  # pragma: no cover — would only fire if the conflicting
            # row was concurrently deleted; the schema has no DELETE path on
            # instruments today, so this is a defensive belt-and-braces raise.
            raise LookupError(
                f"INSERT conflicted on canonical_id={canonical_id!r} but no row found"
            )
        return inst

    async def _upsert_alias(
        self,
        *,
        source: str,
        raw_symbol: str,
        instrument_id: int,
        meta: MetaDict,
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

    async def find_by_alias(
        self,
        *,
        source: str,
        raw_symbol: str,
    ) -> int | None:
        """Pure SELECT over ``symbol_aliases``; no upsert, no lock.

        Returns the resolved ``instruments.id`` or ``None`` when no alias
        row exists. Use this from the risk gate — the gate must NOT author
        instruments at evaluation time.
        """
        result = await self._session.execute(
            select(SymbolAlias.instrument_id)
            .where(SymbolAlias.source == source)
            .where(SymbolAlias.raw_symbol == raw_symbol)
        )
        row = result.first()
        return int(row[0]) if row is not None else None

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
        except (SQLAlchemyError, LookupError) as exc:
            # Tuple parens + `as exc` binding are load-bearing: `ruff format`
            # strips bare-tuple parens, turning `except (A, B):` into Py2-style
            # `except A, B:` (catches only A, binds it to local name B). The
            # `as exc` clause makes ruff keep the parens.
            _log.warning(
                "instrument_resolver.from_legacy_failed",
                broker_id=broker_id,
                raw_symbol=raw_symbol,
                exchange=exchange,
                currency=currency,
                exc_info=exc,
            )
            return None
