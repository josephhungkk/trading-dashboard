"""Concurrency stress for InstrumentResolver — CRIT-3 mitigation.

≥50 concurrent ``resolve_or_create()`` calls for the same novel canonical_id
must produce exactly 1 instrument row + 1 alias row + zero exceptions, even
when the in-process asyncio.Lock cache is empty on first hit.

The two-layer guard (per-canonical_id ``asyncio.Lock`` + DB-side
``INSERT ... ON CONFLICT DO NOTHING``) serialises same-symbol concurrent
callers through one lock; cross-process safety is provided by the unique
index on ``instruments.canonical_id`` + composite PK on
``symbol_aliases(source, raw_symbol)``.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass, Instrument, SymbolAlias
from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_concurrent_resolve_no_dup(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)
    canonical = "stock:NVDA:US"

    async def one() -> Instrument:
        return await resolver.resolve_or_create(
            canonical_id=canonical,
            source="schwab",
            raw_symbol="NVDA",
            asset_class=AssetClass.STOCK,
            primary_exchange="NASDAQ",
            currency="USD",
        )

    results = await asyncio.gather(*[one() for _ in range(50)])
    assert len({r.id for r in results}) == 1

    inst_count = await db_session.execute(
        text("SELECT count(*) FROM instruments WHERE canonical_id = :c"),
        {"c": canonical},
    )
    assert inst_count.scalar_one() == 1

    alias_count = await db_session.execute(
        text("SELECT count(*) FROM symbol_aliases WHERE source='schwab' AND raw_symbol='NVDA'")
    )
    assert alias_count.scalar_one() == 1

    await db_session.rollback()


@pytest.mark.asyncio
async def test_concurrent_resolve_three_sources(db_session: AsyncSession) -> None:
    """Mixed-source bombardment for the same canonical_id: one instrument + 3 aliases."""
    resolver = InstrumentResolver(db_session)
    canonical = "idx:SPX:US"

    async def call(source: str, raw: str) -> Instrument:
        return await resolver.resolve_or_create(
            canonical_id=canonical,
            source=source,
            raw_symbol=raw,
            asset_class=AssetClass.INDEX,
            primary_exchange="CBOE",
            currency="USD",
        )

    coros = []
    for _ in range(50):
        coros.append(call("schwab", "$SPX"))
        coros.append(call("ibkr", "SPX"))
        coros.append(call("yfinance", "^GSPC"))

    results = await asyncio.gather(*coros)
    assert len({r.id for r in results}) == 1

    aliases_q = await db_session.execute(
        select(SymbolAlias).join(Instrument).where(Instrument.canonical_id == canonical)
    )
    rows = aliases_q.scalars().all()
    assert {a.source for a in rows} == {"schwab", "ibkr", "yfinance"}
    assert len(rows) == 3

    await db_session.rollback()
