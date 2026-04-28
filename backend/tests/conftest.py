"""Pytest fixtures."""

# Top-level registration: pytest 9 requires `pytest_plugins` to live in the
# rootdir conftest, never in nested conftests. The shared ``session`` fixture
# is consumed by tests under ``tests/migrations/`` and ``tests/models/``.
pytest_plugins = ("tests.fixtures.db_session",)

import os  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from alembic import command  # noqa: E402

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
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from tests.fixtures.sidecar_servicer import sidecar_client as sidecar_client  # noqa: E402
from tests.fixtures.sidecar_servicer import sidecar_server as sidecar_server  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Ensure the schema exists before any test runs. Locally this is a no-op
    (NUC's `dashboard` DB already has migrations applied); in CI the fresh
    Postgres container starts empty.

    ``config_file_name`` is cleared so Alembic's env.py skips ``fileConfig()``;
    otherwise it resets the root logger and pytest's caplog handler misses
    every subsequent log record in the test run.
    """
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", ""))
    command.upgrade(cfg, "head")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
