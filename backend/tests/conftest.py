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
def _apply_migrations(request: pytest.FixtureRequest) -> None:
    """Ensure the schema exists before any test runs. Locally this is a no-op
    (NUC's `dashboard` DB already has migrations applied); in CI the fresh
    Postgres container starts empty.

    ``config_file_name`` is cleared so Alembic's env.py skips ``fileConfig()``;
    otherwise it resets the root logger and pytest's caplog handler misses
    every subsequent log record in the test run.
    """
    if request.session.items and all(
        item.get_closest_marker("no_db") for item in request.session.items
    ):
        return
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", ""))
    command.upgrade(cfg, "head")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Phase 7a C0 — shared fixtures for chunks B/C/D/E/F (HIGH-5) ──────────────
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.crypto import get_fernet  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.deps import set_config_service  # noqa: E402
from app.services.config import ConfigService  # noqa: E402
from app.services.config_cache import ConfigCache  # noqa: E402


@pytest_asyncio.fixture
async def redis() -> AsyncIterator:
    """In-memory fakeredis for state nonce + pubsub tests."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def db_session_a() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def db_session_b() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def config_service(redis) -> AsyncIterator[ConfigService]:
    """Real ConfigService against the test DB + a fakeredis."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    cc = ConfigCache(redis, "config:invalidate", "config", ttl_seconds=10)
    sc = ConfigCache(redis, "config:invalidate:secrets", "secret", ttl_seconds=10)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    svc = ConfigService(factory, cc, sc, fernet)
    set_config_service(svc)
    yield svc


@pytest_asyncio.fixture(autouse=True)
async def _app_state(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Wire module-level singletons + app.state for HTTP-driving tests.

    Bucket A of the CI debt cleanup: ~55 tests under tests/api/* use the
    bare `client` ASGITransport fixture and hit endpoints whose deps read
    set_config_service(), app.state.redis, app.state.capability_svc — all
    of which are normally wired in app.main.lifespan but never run under
    ASGITransport. Drive the equivalent setup once per test from fakeredis
    + real test DB so endpoints find what they expect.

    Skipped for tests marked @pytest.mark.no_db (pure schema/snapshot work
    that must not touch the DB).
    """
    if request.node.get_closest_marker("no_db") is not None:
        yield
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.order_capability_service import OrderCapabilityService

    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cc = ConfigCache(fake_r, "config:invalidate", "config", ttl_seconds=10)
    sc = ConfigCache(fake_r, "config:invalidate:secrets", "secret", ttl_seconds=10)
    fernet = get_fernet(settings.secret_key, settings.secret_key_prev)
    svc = ConfigService(factory, cc, sc, fernet)
    set_config_service(svc)

    capability_svc = OrderCapabilityService(redis=fake_r, db_factory=factory)
    app.state.redis = fake_r
    app.state.capability_svc = capability_svc

    # Stub broker_registry + account_service so endpoint tests don't hit
    # "broker layer not yet configured" 503. Use spec= so attribute access
    # is constrained to real class surface — bare MagicMock returns Mock
    # for any attribute, which then breaks comparisons (`'>' not
    # supported between instances of 'AsyncMock' and 'int'`) and dict
    # indexing. Tests that need real broker behavior patch via
    # app.dependency_overrides or @patch().
    from app.core.deps import set_account_service, set_broker_registry
    from app.services.brokers import AccountService, BrokerRegistry

    set_broker_registry(MagicMock(spec=BrokerRegistry))
    set_account_service(MagicMock(spec=AccountService))

    try:
        yield
    finally:
        await fake_r.aclose()


@pytest_asyncio.fixture
async def test_client_admin() -> AsyncIterator[AsyncClient]:
    """Async client that injects a fake admin Cf-Access-Jwt-Assertion via
    monkeypatched verifier."""
    from app.core import deps as deps_mod

    # Save the original so subsequent tests aren't auth-bypassed (was the
    # root cause of test_admin_auth.py 200==401 failures).
    original_verify = deps_mod._verifier.verify
    deps_mod._verifier.verify = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(email="admin@test.local", kind="cf_access_jwt"),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            client.headers["Cf-Access-Jwt-Assertion"] = "test-token"
            yield client
    finally:
        deps_mod._verifier.verify = original_verify  # type: ignore[method-assign]


@pytest_asyncio.fixture
async def test_client_no_auth() -> AsyncIterator[AsyncClient]:
    """Async client without the admin JWT header."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def sidecar_stubs() -> dict[str, MagicMock]:
    """Per-label gRPC sidecar stubs for tests."""
    return {
        "schwab": MagicMock(),
        "isa-live": MagicMock(),
        "futu": MagicMock(),
    }


@pytest.fixture
def mock_brokers(sidecar_stubs) -> dict[str, AsyncMock]:
    """Per-label broker-client async mocks."""
    return {
        "schwab": AsyncMock(),
        "isa-live": AsyncMock(),
        "futu": AsyncMock(),
    }


@pytest.fixture
def mock_sidecar_configure(sidecar_stubs) -> AsyncMock:
    sidecar_stubs["schwab"].Configure = AsyncMock()
    return sidecar_stubs["schwab"].Configure
