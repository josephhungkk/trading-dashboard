"""End-to-end admin router tests — auth dep overridden."""

from collections.abc import AsyncIterator

import fakeredis.aioredis as fakeredis_async
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.crypto import get_fernet
from app.core.deps import get_config, require_admin_jwt
from app.main import app
from app.services.config import ConfigService
from app.services.config_cache import ConfigCache


@pytest.fixture(scope="module")
def engine():
    return create_async_engine(settings.database_url, echo=False)


@pytest.fixture(scope="module")
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(session_factory):
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_config"))
        await s.execute(text("DELETE FROM app_secrets"))
        await s.commit()


@pytest.fixture
async def client(session_factory) -> AsyncIterator[TestClient]:
    r = fakeredis_async.FakeRedis(decode_responses=False)
    cache = ConfigCache(r, "config:invalidate", "config", ttl_seconds=60)
    secrets_cache = ConfigCache(r, "config:invalidate:secrets", "secret", ttl_seconds=60)
    fernet = get_fernet("test-key-stable", None)
    service = ConfigService(session_factory, cache, secrets_cache, fernet)

    app.dependency_overrides[require_admin_jwt] = lambda: AdminIdentity(
        email="test@example.com", kind="user", claims={}
    )
    app.dependency_overrides[get_config] = lambda: service

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    await r.aclose()


def test_list_empty(client):
    assert client.get("/api/admin/config").json() == []


def test_list_after_inserts(client):
    for i in range(3):
        client.post(
            "/api/admin/config",
            json={"namespace": "a", "key": f"k{i}", "value": f"v{i}", "value_type": "str"},
        )
    assert len(client.get("/api/admin/config").json()) == 3


def test_list_namespace_filter(client):
    client.post("/api/admin/config", json={"namespace": "a", "key": "k", "value": "v"})
    client.post("/api/admin/config", json={"namespace": "b", "key": "k", "value": "v"})
    assert {e["namespace"] for e in client.get("/api/admin/config?namespace=a").json()} == {"a"}


def test_get_existing(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = client.get("/api/admin/config/n/k")
    assert resp.status_code == 200
    assert resp.json()["value"] == "v"


def test_get_missing_404(client):
    assert client.get("/api/admin/config/absent/k").status_code == 404


def test_post_valid_201(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "x", "key": "y", "value": "z", "value_type": "str"},
    )
    assert resp.status_code == 201


def test_post_json_value_stored(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "cfg", "value": {"nested": 1}, "value_type": "json"},
    )
    assert resp.status_code == 201
    assert resp.json()["value"] == {"nested": 1}


def test_post_invalid_value_type_422(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "FLOAT"},
    )
    assert resp.status_code == 422


def test_post_invalid_namespace_pattern_422(client):
    resp = client.post(
        "/api/admin/config",
        json={"namespace": "UpperCase", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422


def test_post_duplicate_409(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    resp = client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v2"})
    assert resp.status_code == 409


def test_put_creates_if_missing(client):
    resp = client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200


def test_put_updates_existing(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v1"})
    resp = client.put(
        "/api/admin/config/n/k",
        json={"namespace": "n", "key": "k", "value": "v2", "value_type": "str"},
    )
    assert resp.json()["value"] == "v2"


def test_put_body_ns_mismatch_url_422(client):
    resp = client.put(
        "/api/admin/config/foo/k",
        json={"namespace": "bar", "key": "k", "value": "v"},
    )
    assert resp.status_code == 422
    assert "mismatch" in resp.json()["detail"].lower()


def test_put_body_omits_ns_fills_from_url(client):
    resp = client.put(
        "/api/admin/config/n/k",
        json={"value": "v", "value_type": "str"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "n"


def test_delete_existing_204(client):
    client.post("/api/admin/config", json={"namespace": "n", "key": "k", "value": "v"})
    assert client.delete("/api/admin/config/n/k").status_code == 204


def test_delete_missing_also_204(client):
    assert client.delete("/api/admin/config/absent/key").status_code == 204


def test_post_secret_metadata_only_in_response(client):
    resp = client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "sensitive", "value_type": "str"},
    )
    assert resp.status_code == 201
    assert "value" not in resp.json()


def test_get_secret_metadata_no_plaintext(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "secret", "value_type": "str"},
    )
    resp = client.get("/api/admin/secrets/s/k")
    assert resp.status_code == 200
    assert "value" not in resp.json()


def test_list_secrets_metadata_only(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "x", "value_type": "str"},
    )
    resp = client.get("/api/admin/secrets")
    assert resp.status_code == 200
    assert all("value" not in e for e in resp.json())


def test_reveal_returns_plaintext_and_nostore_header(client):
    client.post(
        "/api/admin/secrets",
        json={"namespace": "s", "key": "k", "value": "p@ssw0rd", "value_type": "str"},
    )
    resp = client.post("/api/admin/secrets/s/k/reveal")
    assert resp.status_code == 200
    assert resp.json()["value"] == "p@ssw0rd"
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_reveal_missing_404(client):
    assert client.post("/api/admin/secrets/absent/k/reveal").status_code == 404


def test_delete_secret_idempotent(client):
    client.post("/api/admin/secrets", json={"namespace": "s", "key": "k", "value": "x"})
    assert client.delete("/api/admin/secrets/s/k").status_code == 204
    assert client.delete("/api/admin/secrets/s/k").status_code == 204
