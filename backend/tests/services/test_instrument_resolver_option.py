"""Tests for InstrumentResolver option creation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import AssetClass
from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_find_or_create_option_creates_row_with_canonical_id(
    db_session: AsyncSession,
) -> None:
    resolver = InstrumentResolver(db_session)

    inst = await resolver.find_or_create_option(
        "stock:SPY:US",
        "CALL",
        Decimal("450.00"),
        date(2026, 6, 20),
        "ARCA",
        multiplier=100,
        broker_id="ibkr",
    )

    assert inst.canonical_id == "option:stock:SPY:US:CALL:450.00:2026-06-20"
    assert inst.asset_class == AssetClass.OPTION
    assert inst.meta["asset_class"] == "OPTION"
    assert inst.meta["underlying_canonical_id"] == "stock:SPY:US"
    assert inst.meta["option_type"] == "CALL"
    assert inst.meta["strike"] == "450.00"
    assert inst.meta["expiry"] == "2026-06-20"
    assert inst.meta["multiplier"] == 100

    await db_session.rollback()


@pytest.mark.asyncio
async def test_find_or_create_option_is_idempotent(db_session: AsyncSession) -> None:
    resolver = InstrumentResolver(db_session)

    inst1 = await resolver.find_or_create_option(
        "stock:SPY:US",
        "CALL",
        Decimal("450.00"),
        date(2026, 6, 20),
        "ARCA",
        multiplier=100,
    )
    inst2 = await resolver.find_or_create_option(
        "stock:SPY:US",
        "CALL",
        Decimal("450.00"),
        date(2026, 6, 20),
        "ARCA",
        multiplier=100,
    )

    assert inst1.id == inst2.id

    await db_session.rollback()


def test_build_option_canonical_id_format() -> None:
    canonical_id = InstrumentResolver._build_option_canonical_id(
        "stock:SPY:US",
        "CALL",
        Decimal("450.00"),
        date(2026, 6, 20),
    )

    assert canonical_id == "option:stock:SPY:US:CALL:450.00:2026-06-20"
