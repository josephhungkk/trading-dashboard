"""Shared async-session fixture for tests that exercise live SQL.

Yields a session inside an outer transaction the fixture rolls back on
teardown — guaranteed isolation even on success paths. Tests that need
to recover from an IntegrityError must wrap the offending statement in
``async with session.begin_nested():``.

Lives at module scope (not in a conftest.py) so multiple consumer
locations can register it via ``pytest_plugins = ("tests.fixtures.db_session",)``
without the duplicate-registration error pytest 9 raises when one
conftest is auto-discovered AND referenced explicitly via
``pytest_plugins``.
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
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            await s.begin()
            try:
                yield s
            finally:
                await s.rollback()
    finally:
        await engine.dispose()
