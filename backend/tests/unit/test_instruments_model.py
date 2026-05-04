"""Instrument + SymbolAlias ORM model tests (Phase 7b.1)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass, Instrument, SymbolAlias


@pytest.mark.asyncio
async def test_instrument_round_trip(db_session: AsyncSession):
    """Insert + fetch round-trip preserves all columns including JSONB meta."""
    inst = Instrument(
        canonical_id="stock:AAPL:US",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
        display_name="Apple Inc.",
        meta={"isin": "US0378331005", "sector": "Technology"},
    )
    db_session.add(inst)
    await db_session.flush()

    fetched = await db_session.get(Instrument, inst.id)
    assert fetched is not None
    assert fetched.canonical_id == "stock:AAPL:US"
    assert fetched.asset_class == AssetClass.STOCK
    assert fetched.primary_exchange == "NASDAQ"
    assert fetched.currency == "USD"
    assert fetched.display_name == "Apple Inc."
    assert fetched.meta["sector"] == "Technology"
    assert fetched.meta["isin"] == "US0378331005"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None

    # Roll back to clean session state for fixture teardown — avoids the
    # asyncpg-connection-cleanup race that fires "Event loop is closed" when
    # the next test acquires a fresh session before this one's transaction
    # has fully terminated.
    await db_session.rollback()


@pytest.mark.asyncio
async def test_symbol_alias_fk_cascade(db_session: AsyncSession):
    """Deleting an instrument cascades to its aliases via DB-level CASCADE."""
    inst = Instrument(
        canonical_id="idx:SPX:US",
        asset_class=AssetClass.INDEX,
        primary_exchange="CBOE",
        currency="USD",
    )
    db_session.add(inst)
    await db_session.flush()

    db_session.add_all(
        [
            SymbolAlias(source="schwab", raw_symbol="$SPX", instrument_id=inst.id, meta={}),
            SymbolAlias(
                source="ibkr",
                raw_symbol="SPX",
                instrument_id=inst.id,
                meta={"exchange": "CBOE", "sec_type": "IND"},
            ),
        ]
    )
    await db_session.flush()

    await db_session.delete(inst)
    await db_session.flush()

    result = await db_session.execute(
        select(SymbolAlias).where(SymbolAlias.instrument_id == inst.id)
    )
    assert result.scalars().all() == []

    await db_session.rollback()


@pytest.mark.asyncio
async def test_symbol_alias_composite_pk(db_session: AsyncSession):
    """Composite (source, raw_symbol) PK rejects duplicates."""
    inst = Instrument(
        canonical_id="stock:0700:HK",
        asset_class=AssetClass.STOCK,
        primary_exchange="HKEX",
        currency="HKD",
    )
    db_session.add(inst)
    await db_session.flush()

    db_session.add(SymbolAlias(source="futu", raw_symbol="HK.00700", instrument_id=inst.id))
    await db_session.flush()

    db_session.add(SymbolAlias(source="futu", raw_symbol="HK.00700", instrument_id=inst.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_canonical_id_uniqueness(db_session: AsyncSession):
    """Two instruments with the same canonical_id → IntegrityError."""
    inst1 = Instrument(
        canonical_id="stock:GOOG:US",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    db_session.add(inst1)
    await db_session.flush()

    inst2 = Instrument(
        canonical_id="stock:GOOG:US",
        asset_class=AssetClass.STOCK,
        primary_exchange="NASDAQ",
        currency="USD",
    )
    db_session.add(inst2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_relationship_navigation(db_session: AsyncSession):
    """Instrument.aliases relationship returns the right SymbolAlias rows."""
    inst = Instrument(
        canonical_id="stock:VOD:UK",
        asset_class=AssetClass.STOCK,
        primary_exchange="LSE",
        currency="GBP",
    )
    inst.aliases = [
        SymbolAlias(source="ibkr", raw_symbol="VOD", meta={"currency_hint": "GBp"}),
        SymbolAlias(source="yfinance", raw_symbol="VOD.L", meta={}),
    ]
    db_session.add(inst)
    await db_session.flush()

    fetched = await db_session.get(Instrument, inst.id)
    aliases = sorted(fetched.aliases, key=lambda a: a.source)
    assert [a.source for a in aliases] == ["ibkr", "yfinance"]
    assert aliases[0].meta["currency_hint"] == "GBp"

    await db_session.rollback()
