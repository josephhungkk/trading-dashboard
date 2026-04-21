"""FastAPI dependency providers.

Phase 0 only ships the DB session provider. Auth and config-service deps
are added in Phase 2.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
