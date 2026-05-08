"""Phase 9.5 retro — verify migration 0029 + 0029a effects.

Tests:
1. broker_id_enum includes 'alpaca' — INSERT with broker_id='alpaca' succeeds.
2. No other shipped enum values were dropped (ibkr, futu, schwab still present).
3. watchlist_entries has a UNIQUE constraint on (broker_id, symbol).
4. watchlist_entries rejects duplicate (broker_id, symbol) rows.

Marks: asyncio (auto via pytest.ini), integration, no_db (raw SQL; bypasses
autouse clean_tables fixture to avoid wiping the enum type).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy.exc
from sqlalchemy import text

from app.core.db import engine

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.no_db]


@pytest.mark.asyncio
async def test_broker_id_enum_includes_alpaca() -> None:
    """INSERT a broker_accounts row with broker_id='alpaca' must succeed."""
    account_number = f"ALPACA_TEST_{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(
                    """
                    INSERT INTO broker_accounts
                        (broker_id, account_number, mode, gateway_label,
                         currency_base, last_seen_via)
                    VALUES
                        (CAST('alpaca' AS broker_id_enum), :acct, 'paper',
                         'alpaca-paper', 'USD', 'alpaca-paper')
                    """
                ),
                {"acct": account_number},
            )
        finally:
            await conn.execute(
                text("DELETE FROM broker_accounts WHERE account_number = :acct"),
                {"acct": account_number},
            )


@pytest.mark.asyncio
async def test_broker_id_enum_existing_values_present() -> None:
    """ibkr, futu, schwab, and alpaca must all be valid enum values."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT enumlabel
                  FROM pg_enum
                  JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                 WHERE pg_type.typname = 'broker_id_enum'
                 ORDER BY enumsortorder
                """
            )
        )
        labels = {row[0] for row in result.fetchall()}

    expected = {"ibkr", "futu", "schwab", "alpaca"}
    missing = expected - labels
    assert not missing, f"broker_id_enum is missing values: {missing}"


@pytest.mark.asyncio
async def test_watchlist_entries_unique_constraint_exists() -> None:
    """UNIQUE index watchlist_entries_broker_symbol_uq must exist in pg_indexes."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE tablename = 'watchlist_entries'
                   AND indexname = 'watchlist_entries_broker_symbol_uq'
                """
            )
        )
        row = result.fetchone()
    assert row is not None, (
        "UNIQUE index watchlist_entries_broker_symbol_uq not found — "
        "migration 0029a may not have run"
    )


@pytest.mark.asyncio
async def test_watchlist_entries_rejects_duplicate() -> None:
    """Inserting a duplicate (broker_id, symbol) pair must raise IntegrityError."""
    broker_id = "ibkr"
    symbol = f"DUPTEST_{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(
                    """
                    INSERT INTO watchlist_entries (broker_id, symbol, exchange, currency)
                    VALUES (:broker_id, :symbol, 'XLON', 'GBP')
                    """
                ),
                {"broker_id": broker_id, "symbol": symbol},
            )
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(
                    text(
                        """
                        INSERT INTO watchlist_entries (broker_id, symbol, exchange, currency)
                        VALUES (:broker_id, :symbol, 'XLON', 'GBP')
                        """
                    ),
                    {"broker_id": broker_id, "symbol": symbol},
                )
        finally:
            await conn.execute(
                text(
                    "DELETE FROM watchlist_entries"
                    " WHERE broker_id = :broker_id AND symbol = :symbol"
                ),
                {"broker_id": broker_id, "symbol": symbol},
            )
