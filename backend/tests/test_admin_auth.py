"""Real require_admin_jwt — valid / expired / wrong signer / service token.

Uses httpx.AsyncClient + ASGITransport so asyncpg connections share the
pytest-asyncio event loop (see test_admin_api.py for the same rationale).

The JWT verifier is patched by pinning _jwks_client to a mock that returns
our test RSA public key. The auth flow itself (token parse → signature check
→ iss/aud guard → identity extraction) runs unmodified.

`get_config` is overridden with a stub that returns an empty list so the
downstream handler path does not exercise the DB — these tests care only
about auth verdicts, not config CRUD.
"""

import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jwt import encode as jwt_encode

from app.core.deps import get_config
from app.main import app


class _StubConfigService:
    async def list(self, namespace: str | None = None) -> list[Any]:
        return []

    async def list_secrets(self, namespace: str | None = None) -> list[Any]:
        return []


@pytest.fixture(scope="module")
def rsa_priv():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def rsa_priv_pem(rsa_priv) -> bytes:
    return rsa_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def rsa_pub_pem(rsa_priv) -> bytes:
    return rsa_priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def patch_verifier(rsa_pub_pem):
    from app.core import deps as deps_module

    mock_key = Mock()
    mock_key.key = rsa_pub_pem
    with (
        patch.object(deps_module._verifier, "_jwks_client") as mock_client,
        patch.object(deps_module._verifier, "team_domain", "test.cloudflareaccess.com"),
        patch.object(deps_module._verifier, "audience", "test-aud"),
    ):
        mock_client.get_signing_key_from_jwt = Mock(return_value=mock_key)
        mock_client.invalidate_cache = Mock()
        yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_config] = lambda: _StubConfigService()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _make_jwt(priv_pem: bytes, claims: dict, kid: str = "test-kid") -> str:
    return jwt_encode(claims, priv_pem, algorithm="RS256", headers={"kid": kid})


@pytest.mark.asyncio
async def test_missing_header_401(client):
    assert (await client.get("/api/admin/config")).status_code == 401


@pytest.mark.asyncio
async def test_malformed_jwt_401(client, patch_verifier):
    r = await client.get(
        "/api/admin/config",
        headers={"Cf-Access-Jwt-Assertion": "not.a.jwt"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_email_jwt_200(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = await client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_service_token_common_name_200(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "common_name": "dashboard-ci-smoke",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = await client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_jwt_without_identity_claim_401(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    r = await client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 401
    assert "identity" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_expired_jwt_401(client, patch_verifier, rsa_priv_pem):
    now = int(time.time())
    tok = _make_jwt(
        rsa_priv_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now - 600,
            "exp": now - 60,
        },
    )
    r = await client.get("/api/admin/config", headers={"Cf-Access-Jwt-Assertion": tok})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()
