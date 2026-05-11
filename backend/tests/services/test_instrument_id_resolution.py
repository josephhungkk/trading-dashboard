"""Phase 10a.5 B1 + B2: instrument_id resolution from conid.

Verifies the read-only ``InstrumentResolver.find_by_alias`` path:
- happy path: alias row exists -> returns the linked instrument_id
- miss: no alias row -> returns None (does NOT create)
- no side effects: SELECT-only, row count unchanged
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.quotes.instrument_resolver import InstrumentResolver


@pytest.mark.asyncio
async def test_find_by_alias_happy_path(db_session) -> None:
    """An existing alias row resolves to its instrument_id."""
    iid = (
        await db_session.execute(
            text(
                "INSERT INTO instruments (canonical_id, asset_class, "
                "primary_exchange, currency) "
                "VALUES ('AAPL.US', 'stock', 'NASDAQ', 'USD') RETURNING id"
            )
        )
    ).scalar_one()
    await db_session.execute(
        text(
            "INSERT INTO symbol_aliases (source, raw_symbol, instrument_id) "
            "VALUES ('ibkr', '265598', :iid)"
        ),
        {"iid": iid},
    )
    await db_session.commit()

    resolver = InstrumentResolver(db_session)
    result = await resolver.find_by_alias(source="ibkr", raw_symbol="265598")
    assert result == iid


@pytest.mark.asyncio
async def test_find_by_alias_returns_none_when_missing(db_session) -> None:
    """Unknown (source, raw_symbol) returns None."""
    resolver = InstrumentResolver(db_session)
    result = await resolver.find_by_alias(source="ibkr", raw_symbol="999999999")
    assert result is None


@pytest.mark.asyncio
async def test_find_by_alias_does_not_create_rows(db_session) -> None:
    """find_by_alias is pure SELECT — no aliases created on miss."""
    before = (await db_session.execute(text("SELECT COUNT(*) FROM symbol_aliases"))).scalar_one()
    resolver = InstrumentResolver(db_session)
    await resolver.find_by_alias(source="ibkr", raw_symbol="999999999")
    after = (await db_session.execute(text("SELECT COUNT(*) FROM symbol_aliases"))).scalar_one()
    assert before == after
