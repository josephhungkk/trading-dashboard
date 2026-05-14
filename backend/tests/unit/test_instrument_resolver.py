"""InstrumentResolver — resolve-or-create with race-safe SQL + asyncio.Lock.

Covers Phase 7b.1 CRIT-3 in-process layer + DB-layer ON CONFLICT shape.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass
from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_resolve_or_create_first_observation(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)
    # Use a test-only canonical_id to avoid collision with e2e tests that seed
    # real tickers (e.g. "stock:AAPL:US") via resolve_or_create in orders_service.
    inst = await resolver.resolve_or_create(
        canonical_id="stock:AAPL_UNIT_TEST:US",
        source="schwab",
        raw_symbol="AAPL_UNIT_TEST",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
        meta={"display_name": "Apple Inc."},
    )

    assert inst.canonical_id == "stock:AAPL_UNIT_TEST:US"
    assert inst.asset_class == AssetClass.STOCK
    assert inst.primary_exchange == "NASDAQ"
    assert inst.currency == "USD"

    aliases = await resolver.list_aliases(inst.id)
    assert len(aliases) == 1
    assert aliases[0].source == "schwab"
    assert aliases[0].raw_symbol == "AAPL_UNIT_TEST"

    await db_session.rollback()


@pytest.mark.asyncio
async def test_resolve_or_create_idempotent(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)
    inst1 = await resolver.resolve_or_create(
        canonical_id="stock:MSFT:US",
        source="schwab",
        raw_symbol="MSFT",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    inst2 = await resolver.resolve_or_create(
        canonical_id="stock:MSFT:US",
        source="schwab",
        raw_symbol="MSFT",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    assert inst1.id == inst2.id

    aliases = await resolver.list_aliases(inst1.id)
    assert len(aliases) == 1

    await db_session.rollback()


@pytest.mark.asyncio
async def test_new_alias_for_existing_instrument(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:TSLA:US",
        source="schwab",
        raw_symbol="TSLA",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    inst2 = await resolver.resolve_or_create(
        canonical_id="stock:TSLA:US",
        source="ibkr",
        raw_symbol="TSLA",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
        alias_meta={"conid": 76792991, "sec_type": "STK"},
    )
    assert inst.id == inst2.id

    aliases = await resolver.list_aliases(inst.id)
    assert {a.source for a in aliases} == {"schwab", "ibkr"}

    ibkr_alias = next(a for a in aliases if a.source == "ibkr")
    assert ibkr_alias.meta["conid"] == 76792991
    assert ibkr_alias.meta["sec_type"] == "STK"

    await db_session.rollback()


@pytest.mark.asyncio
async def test_uk_pence_normalization_hint(db_session: AsyncSession) -> None:
    """LSE GBp metadata is preserved on the alias (used by sidecar for /100 guard)."""
    resolver = InstrumentResolver(db_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:VOD:UK",
        source="ibkr",
        raw_symbol="VOD",
        asset_class=AssetClass.STOCK,
        primary_exchange="LSE",
        currency="GBP",
        alias_meta={"exchange": "LSE", "sec_type": "STK", "currency_hint": "GBp"},
    )

    aliases = await resolver.list_aliases(inst.id)
    assert len(aliases) == 1
    assert aliases[0].meta["currency_hint"] == "GBp"
    assert aliases[0].meta["exchange"] == "LSE"

    await db_session.rollback()


@pytest.mark.asyncio
async def test_alias_idempotent(db_session: AsyncSession) -> None:
    """Duplicate (source, raw_symbol) alias inserts are no-ops, not errors."""
    resolver = InstrumentResolver(db_session)
    inst = await resolver.resolve_or_create(
        canonical_id="stock:NFLX:US",
        source="schwab",
        raw_symbol="NFLX",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    await resolver.resolve_or_create(
        canonical_id="stock:NFLX:US",
        source="schwab",
        raw_symbol="NFLX",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )

    aliases = await resolver.list_aliases(inst.id)
    assert len(aliases) == 1

    await db_session.rollback()


@pytest.mark.asyncio
async def test_meta_preserved_on_create(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)
    inst = await resolver.resolve_or_create(
        canonical_id="idx:HSI:HK",
        source="futu",
        raw_symbol="HK.800000",
        asset_class=AssetClass.INDEX,
        primary_exchange="HKEX",
        currency="HKD",
        meta={"display_name": "Hang Seng Index"},
    )
    assert inst.meta.get("display_name") == "Hang Seng Index"
    assert inst.asset_class == AssetClass.INDEX

    await db_session.rollback()
