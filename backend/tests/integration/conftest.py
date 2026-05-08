"""Integration test conftest — overrides _apply_migrations + shared instrument fixtures.

The dev NUC DB does not have TimescaleDB installed locally, so Alembic cannot
run migrations 0023+ (which require CREATE EXTENSION timescaledb). The needed
tables (chart_layouts, instruments, app_config) are created manually for
development. In CI the Timescale Docker image is used and full migrations run.

This conftest replaces the autouse session-scope _apply_migrations fixture
with a no-op so integration tests can run against the already-migrated dev DB,
and provides shared instrument-seeding fixtures used across alembic_002[4-7]
and bar/chart-layout integration tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override — dev DB tables created manually; CI runs full migrations."""
    return


@pytest_asyncio.fixture
async def seed_instrument_aapl() -> Callable[[AsyncSession], Awaitable[int]]:
    """Insert a single AAPL test instrument and return its id.

    Used by tests/integration/test_alembic_0024.py, _0026.py, _0027.py, and
    test_active_set_query.py — they all need an instrument row to satisfy
    FK constraints on chart_layouts / bars / bar_backfill_jobs.
    """

    async def _seed(session: AsyncSession) -> int:
        result = await session.execute(
            text(
                """
                INSERT INTO instruments (canonical_id, asset_class, primary_exchange, currency)
                VALUES ('stock:AAPL:US', 'STOCK', 'NASDAQ', 'USD')
                ON CONFLICT (canonical_id) DO UPDATE SET updated_at = now()
                RETURNING id
                """
            )
        )
        await session.flush()
        return int(result.scalar_one())

    return _seed


@pytest_asyncio.fixture
async def bulk_seed_1500_instruments() -> Callable[[AsyncSession], Awaitable[int]]:
    """Insert 1500 synthetic test instruments — used by active-set cap tests."""

    async def _seed(session: AsyncSession) -> int:
        await session.execute(
            text(
                """
                INSERT INTO instruments (canonical_id, asset_class, primary_exchange, currency)
                SELECT
                    'stock:TEST' || i || ':US',
                    'STOCK',
                    'NASDAQ',
                    'USD'
                FROM generate_series(1, 1500) AS s(i)
                ON CONFLICT (canonical_id) DO NOTHING
                """
            )
        )
        await session.flush()
        return 1500

    return _seed
