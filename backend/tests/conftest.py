"""Pytest fixtures."""

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

# Env vars set before importing app (pydantic-settings reads at import time).
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-chars-ok")
os.environ.setdefault("APP_CORS_ORIGINS", '["http://localhost:5173"]')
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trader:ci@localhost:5432/dashboard",
)
os.environ.setdefault("POSTGRES_POOL_SIZE", "2")
os.environ.setdefault("POSTGRES_MAX_OVERFLOW", "2")
os.environ.setdefault("REDIS_PASSWORD", "ci")
os.environ.setdefault("REDIS_URL", "redis://:ci@localhost:6379/0")

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
