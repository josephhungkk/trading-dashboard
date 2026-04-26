"""Shared fixtures for Alembic migration tests.

Each test runs inside an outer transaction the fixture rolls back on
teardown — guaranteed isolation even on success paths. Tests that need
to recover from an IntegrityError must wrap the offending statement in
``async with session.begin_nested():`` (a savepoint that absorbs the
error without aborting the outer transaction).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session inside an outer transaction that ALWAYS rolls back.

    The fixture-driven rollback is the safety net: tests cannot
    accidentally commit, even on success paths. SQLAlchemy 2.0
    ``async with s.begin()`` commits on normal exit — never use it in
    these tests.
    """
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            await s.begin()  # outer transaction
            try:
                yield s
            finally:
                await s.rollback()
    finally:
        await engine.dispose()
