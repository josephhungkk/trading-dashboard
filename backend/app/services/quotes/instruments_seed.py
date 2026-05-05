"""Seed instruments from legacy broker positions."""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import QUOTE_SEED_SKIPPED_TOTAL
from app.services.quotes.base import country_for_exchange
from app.services.quotes.instrument_resolver import InstrumentResolver

log = structlog.get_logger(__name__)


async def seed_instruments_from_positions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Best-effort one-shot seed of instruments + aliases from positions."""
    async with session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT ba.broker_id, p.symbol, p.primary_exchange, p.currency
                  FROM positions p
                  JOIN broker_accounts ba ON ba.id = p.account_id
                 WHERE p.symbol IS NOT NULL
                   AND p.primary_exchange IS NOT NULL
                   AND p.currency IS NOT NULL;
                """
            )
        )
        resolver = InstrumentResolver(session)
        count = 0
        for row in result.mappings():
            broker_id = str(row["broker_id"])
            symbol = str(row["symbol"])
            exchange = str(row["primary_exchange"])
            currency = str(row["currency"])
            reason = "resolver_fail"
            if not symbol:
                reason = "resolver_fail"
            elif country_for_exchange(exchange) is None:
                reason = "no_country"
            else:
                instrument = await resolver.from_legacy(broker_id, symbol, exchange, currency)
                if instrument is not None:
                    count += 1
                    continue
            QUOTE_SEED_SKIPPED_TOTAL.labels(reason=reason).inc()
        await session.commit()
    log.info("instrument_seed.ok", count=count)
    return count
