"""CF Access JWT verification tests."""

import time
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.cf_access import (
    AdminIdentity,
    CFAccessVerifier,
    NoIdentityClaimError,
    client_ip_in_trusted_nets,
)


@pytest.fixture(scope="module")
def rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture(scope="module")
def rsa_private_pem(rsa_keypair):
    priv, _ = rsa_keypair
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def rsa_public_pem(rsa_keypair):
    _, pub = rsa_keypair
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _issue_jwt(priv_pem: bytes, claims: dict, kid: str = "test-kid") -> str:
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def verifier(rsa_public_pem):
    """Verifier whose JWKS always returns our test public key."""
    v = CFAccessVerifier(
        team_domain="test.cloudflareaccess.com",
        audience="test-aud",
        trusted_dev_nets=[],
        env="prod",
    )
    mock_key = Mock()
    mock_key.key = rsa_public_pem
    v._jwks_client = Mock()
    v._jwks_client.get_signing_key_from_jwt = Mock(return_value=mock_key)
    return v


def test_valid_jwt_with_email_claim(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = verifier.verify(token, client_ip="1.2.3.4")
    assert isinstance(identity, AdminIdentity)
    assert identity.email == "alice@example.com"
    assert identity.kind == "user"


def test_valid_jwt_with_common_name_claim(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "common_name": "dashboard-ci-smoke",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = verifier.verify(token, client_ip="1.2.3.4")
    assert identity.email == "dashboard-ci-smoke"
    assert identity.kind == "service_token"


def test_jwt_with_neither_claim_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(NoIdentityClaimError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_expired_jwt_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "alice@example.com",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now - 600,
            "exp": now - 60,
        },
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_wrong_issuer_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://evil.example.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(jwt.InvalidIssuerError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_wrong_audience_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "wrong-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(token, client_ip="1.2.3.4")


def test_tampered_signature_raises(verifier, rsa_private_pem):
    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    parts = token.split(".")
    parts[2] = parts[2][:-4] + "XXXX"
    bad = ".".join(parts)
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(bad, client_ip="1.2.3.4")


def test_kid_miss_forces_refresh(rsa_public_pem, rsa_private_pem):
    v = CFAccessVerifier(
        team_domain="test.cloudflareaccess.com",
        audience="test-aud",
        trusted_dev_nets=[],
        env="prod",
    )
    mock_key = Mock()
    mock_key.key = rsa_public_pem
    call_count = {"n": 0}

    def side_effect(token):
        call_count["n"] += 1
        if call_count["n"] == 1:
            from jwt.exceptions import PyJWKClientError

            raise PyJWKClientError("unknown kid")
        return mock_key

    v._jwks_client = Mock()
    v._jwks_client.get_signing_key_from_jwt = Mock(side_effect=side_effect)

    now = int(time.time())
    token = _issue_jwt(
        rsa_private_pem,
        {
            "email": "a@b",
            "iss": "https://test.cloudflareaccess.com",
            "aud": "test-aud",
            "iat": now,
            "exp": now + 300,
        },
    )
    identity = v.verify(token, client_ip="1.2.3.4")
    assert identity.email == "a@b"
    assert call_count["n"] == 2
    v._jwks_client.invalidate_cache.assert_called_once()


def test_dev_bypass_when_env_dev_and_ip_in_list(caplog):
    import logging

    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="dev",
    )
    with caplog.at_level(logging.WARNING, logger="app.core.cf_access"):
        identity = v.check_dev_bypass(client_ip="10.10.0.5")
    assert identity is not None
    assert identity.email == "dev@localhost"
    assert identity.kind == "dev-bypass"
    assert any("dev_bypass_granted" in rec.message for rec in caplog.records)
    assert any("10.10.0.5" in rec.message for rec in caplog.records)


def test_dev_bypass_inactive_when_env_dev_but_ip_not_in_list():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="dev",
    )
    assert v.check_dev_bypass(client_ip="88.208.197.219") is None


def test_dev_bypass_inactive_when_env_prod_even_with_ip_match():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=["10.10.0.0/24"],
        env="prod",
    )
    assert v.check_dev_bypass(client_ip="10.10.0.5") is None


def test_dev_bypass_inactive_when_trusted_nets_empty():
    v = CFAccessVerifier(
        team_domain="",
        audience="",
        trusted_dev_nets=[],
        env="dev",
    )
    assert v.check_dev_bypass(client_ip="10.10.0.5") is None


def test_prod_with_trusted_dev_nets_is_a_config_smell(caplog):
    """Startup should log CRITICAL if prod env has non-empty trusted_dev_nets."""
    import logging

    with caplog.at_level(logging.CRITICAL, logger="app.core.cf_access"):
        CFAccessVerifier(
            team_domain="",
            audience="",
            trusted_dev_nets=["10.10.0.0/24"],
            env="prod",
        ).check_startup_config_smell()
    assert any("dev_bypass_config_smell" in rec.message for rec in caplog.records)


def test_client_ip_in_trusted_nets():
    nets = ["10.10.0.0/24", "192.168.1.0/24"]
    assert client_ip_in_trusted_nets("10.10.0.5", nets)
    assert client_ip_in_trusted_nets("192.168.1.254", nets)
    assert not client_ip_in_trusted_nets("8.8.8.8", nets)
    assert not client_ip_in_trusted_nets("10.11.0.1", nets)
    assert not client_ip_in_trusted_nets("invalid-ip", nets)


def test_client_ip_empty_nets_always_false():
    assert not client_ip_in_trusted_nets("10.10.0.5", [])


def test_client_ip_skips_malformed_cidrs():
    """Malformed CIDR entries in nets must be skipped, not crash the check."""
    nets = ["not-a-cidr", "10.10.0.0/24"]
    assert client_ip_in_trusted_nets("10.10.0.5", nets)
    assert not client_ip_in_trusted_nets("8.8.8.8", ["not-a-cidr"])


def test_admin_identity_repr_scrubs_claims():
    """repr(AdminIdentity) must not include claims (could leak iss/aud/exp to logs)."""
    identity = AdminIdentity(
        email="alice@example.com",
        kind="user",
        claims={"iss": "secret-issuer", "aud": "secret-aud", "exp": 12345},
    )
    r = repr(identity)
    assert "alice@example.com" in r
    assert "user" in r
    assert "secret-issuer" not in r
    assert "secret-aud" not in r
    assert "12345" not in r
