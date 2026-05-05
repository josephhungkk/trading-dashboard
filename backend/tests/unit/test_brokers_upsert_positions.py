from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.brokers import base
from app.core.config import settings
from app.services.brokers import BrokerDiscoverer


def _position() -> base.Position:
    return base.Position(
        contract=base.Contract(
            conid="265598",
            symbol="AAPL",
            exchange="NASDAQ",
            currency="USD",
            asset_class="STOCK",
            local_symbol="",
            multiplier="1",
        ),
        quantity="10",
        avg_cost=base.Money(value="150", currency="USD"),
        market_price=base.Money(value="0", currency="USD"),
        market_value=base.Money(value="0", currency="USD"),
        unrealized_pnl=base.Money(value="0", currency="USD"),
        realized_pnl_today=base.Money(value="0", currency="USD"),
        daily_pnl=base.Money(value="0", currency="USD"),
    )


async def _seed_account(conn: AsyncConnection, account_number: str) -> UUID:
    result = await conn.execute(
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
    return UUID(str(result.scalar_one()))


@pytest.mark.asyncio
async def test_upsert_positions_writes_canonical_columns() -> None:
    account_number = f"UTEST_UPSERT_{uuid4().hex[:8]}"
    engine = create_async_engine(
        settings.database_url,
        connect_args={"timeout": 2},
        pool_pre_ping=True,
    )
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            session_factory = async_sessionmaker(
                bind=conn,
                class_=AsyncSession,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                account_id = await _seed_account(conn, account_number)
                async with session_factory() as session:
                    discoverer = BrokerDiscoverer(MagicMock(), MagicMock())
                    await discoverer._upsert_positions(session, account_id, [_position()])
                    row = (
                        await session.execute(
                            text(
                                """
                                SELECT symbol, primary_exchange, canonical_id
                                  FROM positions
                                 WHERE account_id = :account_id AND conid = '265598';
                                """
                            ),
                            {"account_id": account_id},
                        )
                    ).one()
                assert tuple(row) == ("AAPL", "NASDAQ", "stock:AAPL:US")
            finally:
                await tx.rollback()
    except (SQLAlchemyError, TimeoutError, OSError) as exc:
        pytest.skip(f"DATABASE_URL is not reachable: {exc}")
    finally:
        await engine.dispose()
