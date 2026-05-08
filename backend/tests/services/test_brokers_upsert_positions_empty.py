from __future__ import annotations

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

from app.core.config import settings
from app.services.brokers import BrokerDiscoverer


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
async def test_upsert_positions_empty_payload_preserves_existing_rows() -> None:
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
                account_id = await _seed_account(conn, f"UTEST_EMPTY_{uuid4().hex[:8]}")
                await conn.execute(
                    text(
                        """
                        INSERT INTO positions
                            (account_id, conid, qty, avg_cost, currency, multiplier)
                        VALUES
                            (:account_id, '1001', 1, 10, 'USD', 1),
                            (:account_id, '1002', 2, 20, 'USD', 1),
                            (:account_id, '1003', 3, 30, 'USD', 1);
                        """
                    ),
                    {"account_id": account_id},
                )
                async with session_factory() as session:
                    discoverer = BrokerDiscoverer(None, None)  # type: ignore[arg-type]
                    await discoverer._upsert_positions(session, account_id, [], "ibkr")
                    count = (
                        await session.execute(
                            text("SELECT COUNT(*) FROM positions WHERE account_id = :account_id"),
                            {"account_id": account_id},
                        )
                    ).scalar_one()
                assert count == 3
            finally:
                await tx.rollback()
    except (SQLAlchemyError, TimeoutError, OSError) as exc:
        pytest.skip(f"DATABASE_URL is not reachable: {exc}")
    finally:
        await engine.dispose()
