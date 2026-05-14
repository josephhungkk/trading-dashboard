"""End-to-end admin router tests — auth dep overridden.

Uses httpx.AsyncClient + ASGITransport (not starlette TestClient) so asyncpg
connections stay on the pytest-asyncio event loop. TestClient spawns a portal
thread with its own loop, which races with the module-global SQLAlchemy engine.
"""

from collections.abc import AsyncIterator

import fakeredis.aioredis as fakeredis_async
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.deps import get_config, require_admin_jwt
from app.main import app
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache


@pytest.fixture
async def engine():
    eng = create_async_engine(settings.database_url, echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(session_factory):
    # Guardrail: refuse to wipe app_secrets / app_config against the prod
    # NUC database. The original fixture issued a blanket DELETE which
    # nuked the operator-published broker.mtls.* rows on every local
    # pytest run (memory feedback_pytest_prod_db_wipe.md). If DATABASE_URL
    # points at the prod WG IP, raise loudly instead of destroying state.
    db_url = settings.database_url
    if "10.10.0.2" in db_url and "localhost" not in db_url:
        pytest.skip(
            "Refusing to truncate app_config/app_secrets against the prod DB "
            f"({db_url}). Override DATABASE_URL to a local test PG before "
            "running pytest in backend/. See memory feedback_pytest_prod_db_wipe.md."
        )
    async with session_factory() as s:
        # Phase 5b: orders + order_events FK-ordered (events first).
        await s.execute(text("DELETE FROM order_events"))
        await s.execute(text("DELETE FROM orders"))
        # 0046 trigger blocks multi-namespace unfiltered DELETEs; scan and delete per-namespace.
        for ns in (
            (await s.execute(text("SELECT DISTINCT namespace FROM app_config"))).scalars().all()
        ):
            await s.execute(text("DELETE FROM app_config WHERE namespace = :ns"), {"ns": ns})
        for ns in (
            (await s.execute(text("SELECT DISTINCT namespace FROM app_secrets"))).scalars().all()
        ):
            await s.execute(text("DELETE FROM app_secrets WHERE namespace = :ns"), {"ns": ns})
        await s.commit()


@pytest.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)
    fernet = get_fernet("test-key-stable", None)
    service = ConfigService(session_factory, cache, secrets_cache, fernet)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_config] = lambda: service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await r.aclose()


@pytest.mark.asyncio
async def test_list_empty(client):
    assert (await client.get("/api/admin/config")).json() == []


@pytest.mark.asyncio
async def test_list_after_inserts(client):
    for i in range(3):
        await client.post(
            "/api/admin/config",
            json={"namespace": "a", "key": f"k{i}", "value": f"v{i}", "value_type": "str"},
        )
    assert len((await client.get("/api/admin/config")).json()) == 3


@pytest.mark.asyncio
async def test_list_namespace_filter(client):
    await client.post("/api/admin/config", json={"namespace": "a", "key": "k", "value": "v"})
    await client.post("/api/admin/config", json={"namespace": "b", "key": "k", "value": "v"})
    got = (await client.get("/api/admin/config?namespace=a")).json()
    assert {e["namespace"] for e in got} == {"a"}


@pytest.mark.asyncio
async def test_get_existing(client):
    await client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = await client.get("/api/admin/config/n/k")
    assert resp.status_code == 200
    assert resp.json()["value"] == "v"


@pytest.mark.asyncio
async def test_get_missing_404(client):
    assert (await client.get("/api/admin/config/absent/k")).status_code == 404


@pytest.mark.asyncio
async def test_post_valid_201(client):
    resp = await client.post(
        "/api/admin/config",
        json={"namespace": "x", "key": "y", "value": "z", "value_type": "str"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_post_json_value_stored(client):
    resp = await client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "cfg", "value": {"nested": 1}, "value_type": "json"},
    )
    assert resp.status_code == 201
    assert resp.json()["value"] == {"nested": 1}


@pytest.mark.asyncio
async def test_post_invalid_value_type_422(client):
    resp = await client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "FLOAT"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_invalid_namespace_pattern_422(client):
    resp = await client.post(
        "/api/admin/config",
        json={"namespace": "UpperCase", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_duplicate_409(client):
    await client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = await client.post(
        "/api/admin/config", json={"namespace": "n", "key": "k", "value": "v2"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_put_creates_if_missing(client):
    resp = await client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_updates_existing(client):
    await client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v1"})
    resp = await client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v2", "value_type": "str"},
    )
    assert resp.json()["value"] == "v2"


@pytest.mark.asyncio
async def test_put_body_ns_mismatch_url_422(client):
    resp = await client.put(
        "/api/admin/config/foo/k",
        json={"namespace": "bar", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422
    assert "mismatch" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_put_body_omits_ns_fills_from_url(client):
    resp = await client.put(
        "/api/admin/config/n/k",
        json={"value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "n"


@pytest.mark.asyncio
async def test_delete_existing_204(client):
    await client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    assert (await client.delete("/api/admin/config/n/k")).status_code == 204


@pytest.mark.asyncio
async def test_delete_missing_also_204(client):
    assert (await client.delete("/api/admin/config/absent/key")).status_code == 204


@pytest.mark.asyncio
async def test_post_secret_metadata_only_in_response(client):
    resp = await client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "sensitive", "value_type": "str"},
    )
    assert resp.status_code == 201
    assert "value" not in resp.json()


@pytest.mark.asyncio
async def test_get_secret_metadata_no_plaintext(client):
    await client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "secret", "value_type": "str"},
    )
    resp = await client.get("/api/admin/secrets/s/k")
    assert resp.status_code == 200
    assert "value" not in resp.json()


@pytest.mark.asyncio
async def test_list_secrets_metadata_only(client):
    await client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "x", "value_type": "str"},
    )
    resp = await client.get("/api/admin/secrets")
    assert resp.status_code == 200
    assert all("value" not in e for e in resp.json())


@pytest.mark.asyncio
async def test_reveal_returns_plaintext_and_nostore_header(client):
    await client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "p@ssw0rd", "value_type": "str"},
    )
    resp = await client.post("/api/admin/secrets/s/k/reveal")
    assert resp.status_code == 200
    assert resp.json()["value"] == "p@ssw0rd"
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_reveal_missing_404(client):
    assert (await client.post("/api/admin/secrets/absent/k/reveal")).status_code == 404


@pytest.mark.asyncio
async def test_delete_secret_idempotent(client):
    await client.post("/api/admin/secrets", json={"namespace": "s", "key": "k", "value": "x"})
    assert (await client.delete("/api/admin/secrets/s/k")).status_code == 204
    assert (await client.delete("/api/admin/secrets/s/k")).status_code == 204
