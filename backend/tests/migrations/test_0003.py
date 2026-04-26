"""Migration 0003 — broker_accounts_nlv schema constraint tests.

Validates the CHECK constraint behaviour and column defaults documented
in spec §4 (R1, R11). Uses the live test database — Alembic 0003 must
have been applied during fixture setup.

Migration 0002 makes ``mode``, ``gateway_label``, ``currency_base``, and
``last_seen_via`` NOT NULL with no server defaults, so each INSERT must
populate them in addition to the 0003-specific NLV columns under test.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

# Required NOT NULL columns from migration 0002 — every INSERT below
# must populate these so the CHECK constraint, NUMERIC overflow, and
# VARCHAR length rules are the only failure modes under test.
_BASE_COLS = "broker_id, account_number, mode, gateway_label, currency_base, last_seen_via"
_BASE_VALS = "'ibkr', :acct, 'paper', 'isa-paper', 'USD', 'isa-paper'"


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_short(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(IntegrityError, match="broker_accounts_last_nlv_currency_iso3"):
            await s.execute(
                text(
                    f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv_currency) "
                    f"VALUES ({_BASE_VALS}, 'US')"
                ),
                {"acct": "TEST_SHORT"},
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_lowercase(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(IntegrityError, match="broker_accounts_last_nlv_currency_iso3"):
            await s.execute(
                text(
                    f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv_currency) "
                    f"VALUES ({_BASE_VALS}, 'usd')"
                ),
                {"acct": "TEST_LOWER"},
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_padded(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(DBAPIError):
            # VARCHAR(3) raises ``value too long for type character varying(3)``;
            # the CHECK regex would also reject 4-char input. Either way the
            # row must NOT be silently accepted.
            await s.execute(
                text(
                    f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv_currency) "
                    f"VALUES ({_BASE_VALS}, 'USDX')"
                ),
                {"acct": "TEST_LONG"},
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_accepts_iso3(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv, last_nlv_currency) "
                f"VALUES ({_BASE_VALS}, 100, 'USD')"
            ),
            {"acct": "TEST_OK"},
        )
        row = (
            await s.execute(
                text(
                    "SELECT last_nlv, last_nlv_currency FROM broker_accounts "
                    "WHERE account_number = 'TEST_OK'"
                )
            )
        ).first()
        assert row.last_nlv == Decimal("100.00000000")
        assert row.last_nlv_currency == "USD"


@pytest.mark.asyncio
async def test_last_nlv_overflow_rejected(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(DBAPIError, match="overflow"):
            await s.execute(
                text(
                    f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv) "
                    f"VALUES ({_BASE_VALS}, 1e30)"
                ),
                {"acct": "TEST_OVERFLOW"},
            )


@pytest.mark.asyncio
async def test_last_nlv_max_precision_accepted(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                f"INSERT INTO broker_accounts ({_BASE_COLS}, last_nlv, last_nlv_currency) "
                f"VALUES ({_BASE_VALS}, 999999999999.99999999, 'USD')"
            ),
            {"acct": "TEST_PRECISION"},
        )
        row = (
            await s.execute(
                text("SELECT last_nlv FROM broker_accounts WHERE account_number = 'TEST_PRECISION'")
            )
        ).first()
        assert row.last_nlv == Decimal("999999999999.99999999")
