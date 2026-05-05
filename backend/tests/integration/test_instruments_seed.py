from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import engine
from app.core.metrics import QUOTE_SEED_SKIPPED_TOTAL
from app.models.instruments import Instrument
from app.services.quotes.instruments_seed import seed_instruments_from_positions


@pytest.fixture
async def seed_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.connect() as conn:
        tx = await conn.begin()
        factory = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield factory
        finally:
            await tx.rollback()


async def _seed_position(
    session: AsyncSession,
    *,
    account_number: str,
    conid: str,
    symbol: str,
    exchange: str,
) -> None:
    result = await session.execute(
        text(
            """
            INSERT INTO broker_accounts
            (broker_id, account_number, mode, gateway_label, currency_base, last_seen_via)
            VALUES ('ibkr', :account_number, 'paper', 'isa-paper', 'USD', 'isa-paper')
            RETURNING id;
            """
        ),
        {"account_number": account_number},
    )
    account_id = result.scalar_one()
    await session.execute(
        text(
            """
            INSERT INTO positions (
                account_id, conid, qty, avg_cost, currency, multiplier, asset_class,
                symbol, primary_exchange
            )
            VALUES (
                :account_id, :conid, 1, 100, 'USD', 1, 'STOCK', :symbol, :exchange
            );
            """
        ),
        {
            "account_id": account_id,
            "conid": conid,
            "symbol": symbol,
            "exchange": exchange,
        },
    )


@pytest.mark.asyncio
async def test_seed_instruments_from_positions(
    seed_factory: async_sessionmaker[AsyncSession],
) -> None:
    prefix = f"UTEST_SEED_{uuid4().hex[:8]}"
    skipped_before = (
        QUOTE_SEED_SKIPPED_TOTAL.labels(reason="no_country")._value.get()
        + QUOTE_SEED_SKIPPED_TOTAL.labels(reason="resolver_fail")._value.get()
    )
    async with seed_factory() as session:
        await session.execute(text("DELETE FROM positions;"))
        await _seed_position(
            session,
            account_number=f"{prefix}_OK",
            conid=f"{prefix}_1",
            symbol="AAPL",
            exchange="NASDAQ",
        )
        await _seed_position(
            session,
            account_number=f"{prefix}_NO_EXCHANGE",
            conid=f"{prefix}_2",
            symbol="MSFT",
            exchange="",
        )
        await _seed_position(
            session,
            account_number=f"{prefix}_NO_SYMBOL",
            conid=f"{prefix}_3",
            symbol="",
            exchange="NASDAQ",
        )
        await session.commit()

    count = await seed_instruments_from_positions(seed_factory)

    skipped_after = (
        QUOTE_SEED_SKIPPED_TOTAL.labels(reason="no_country")._value.get()
        + QUOTE_SEED_SKIPPED_TOTAL.labels(reason="resolver_fail")._value.get()
    )
    assert count == 1
    assert skipped_after - skipped_before == 2

    async with seed_factory() as session:
        result = await session.execute(
            select(Instrument).where(Instrument.canonical_id == "stock:AAPL:US")
        )
        assert result.scalar_one().primary_exchange == "NASDAQ"
