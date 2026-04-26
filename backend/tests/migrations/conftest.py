"""Shared fixtures for Alembic migration tests.

Mirrors Phase 4's test_0002 engine pattern but exposes an
``async_sessionmaker`` so spec §11 R1+R11 tests can wrap each scenario
in ``async with session_factory() as s, s.begin():`` for automatic
rollback isolation.
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
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield an async_sessionmaker bound to a fresh engine.

    Each yielded session opens a transaction via ``s.begin()`` in the
    test body; the ``with`` exit rolls back unless explicitly committed,
    so test data never leaks between cases.
    """
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
