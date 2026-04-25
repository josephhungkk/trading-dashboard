"""Tests for sidecar.tls (Phase 4 Task 9)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sidecar.tls import (
    _validate_pem_material,
    _verify_crl,
    assert_key_file_permissions,
    clientcert_sha256,
    server_options_for_tls13,
)

# ---------- helpers ----------


def _make_ca_and_key(
    common_name: str = "Test CA",
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(tz=UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _make_leaf(
    ca_cert: x509.Certificate, ca_key: ec.EllipticCurvePrivateKey
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leaf")]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(tz=UTC) + timedelta(days=365))
        .sign(ca_key, hashes.SHA256())
    )
    return cert, leaf_key


def _build_crl(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    *,
    last_update: datetime | None = None,
    next_update: datetime | None = None,
) -> bytes:
    """Build a signed CRL.

    The cryptography builder enforces last_update < next_update at sign time,
    which is why expired-CRL tests pass last_update far in the past.
    """
    last = last_update or datetime.now(tz=UTC) - timedelta(minutes=1)
    nxt = next_update or datetime.now(tz=UTC) + timedelta(days=30)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(last)
        .next_update(nxt)
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )
    return crl.public_bytes(serialization.Encoding.PEM)


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ---------- assert_key_file_permissions ----------


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit check; Windows is TODO(task14)")
def test_key_file_permissions_accepts_owner_only(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")
    os.chmod(key, 0o600)
    assert_key_file_permissions(key)  # must not raise


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit check; Windows is TODO(task14)")
def test_key_file_permissions_rejects_world_readable(tmp_path: Path) -> None:
    """HIGH-5: world-readable private keys must abort startup."""
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")
    os.chmod(key, 0o644)  # group + other read
    with pytest.raises(RuntimeError, match="world-readable"):
        assert_key_file_permissions(key)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit check; Windows is TODO(task14)")
def test_key_file_permissions_rejects_world_writable(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    key.write_bytes(b"unused")
    os.chmod(key, 0o602)  # only world-write bit
    # S_IRWXO = 0o007 — any of r/w/x by 'other' triggers refusal.
    assert key.stat().st_mode & stat.S_IRWXO
    with pytest.raises(RuntimeError):
        assert_key_file_permissions(key)


# ---------- server_options_for_tls13 ----------


def test_server_options_for_tls13_pins_minimum() -> None:
    """CR-4: TLS 1.3 minimum is enforced via channel options, not credentials."""
    options = server_options_for_tls13()
    assert options == [("grpc.tls_minimum_version", 1)]


# ---------- _verify_crl ----------


def test_verify_crl_accepts_signed_crl() -> None:
    ca_cert, ca_key = _make_ca_and_key()
    crl_pem = _build_crl(ca_cert, ca_key)
    result = _verify_crl(crl_pem, _pem(ca_cert))
    assert isinstance(result, x509.CertificateRevocationList)


def test_verify_crl_rejects_wrong_issuer() -> None:
    """CR-3: a CRL signed by a different CA must be refused.

    Distinct CNs ensure the issuer-name check trips before the signature check
    — exercises the issuer-name branch specifically.
    """
    real_ca, _real_key = _make_ca_and_key(common_name="Real CA")
    other_ca, other_key = _make_ca_and_key(common_name="Attacker CA")
    crl_pem = _build_crl(other_ca, other_key)
    with pytest.raises(ValueError, match="issuer"):
        _verify_crl(crl_pem, _pem(real_ca))


def test_verify_crl_rejects_forged_signature() -> None:
    """CR-3: a CRL claiming the right issuer but signed by a different key must fail.

    Hand-craft a CRL whose issuer name matches the real CA but signed with a
    foreign key. This is the exact attack the issuer-name-only check would miss.
    """
    real_ca, _ = _make_ca_and_key()
    _, attacker_key = _make_ca_and_key()
    forged = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(real_ca.subject)  # claim the real CA's name
        .last_update(datetime.now(tz=UTC) - timedelta(minutes=1))
        .next_update(datetime.now(tz=UTC) + timedelta(days=30))
        .sign(private_key=attacker_key, algorithm=hashes.SHA256())
    )
    forged_pem = forged.public_bytes(serialization.Encoding.PEM)
    with pytest.raises(ValueError, match="signature"):
        _verify_crl(forged_pem, _pem(real_ca))


def test_verify_crl_rejects_missing_next_update() -> None:
    """HIGH-4: a CRL with no nextUpdate field is malformed; refuse it.

    The cryptography builder enforces next_update at build time, so we patch
    the loaded CRL's `next_update_utc` property to None to simulate a CRL
    that arrived from an external source (e.g. a manually-built ASN.1 blob).
    """
    ca_cert, ca_key = _make_ca_and_key()
    crl_pem = _build_crl(ca_cert, ca_key)
    with patch.object(
        x509.CertificateRevocationList,
        "next_update_utc",
        new_callable=lambda: property(lambda self: None),
    ), pytest.raises(ValueError, match="nextUpdate"):
        _verify_crl(crl_pem, _pem(ca_cert))


def test_verify_crl_warns_but_accepts_expired() -> None:
    """HIGH-4: a stale CRL warns loudly but does not block startup.

    last_update sits 30 days in the past so next_update can be 1 day in the
    past while still satisfying the builder's last_update < next_update rule.
    """
    ca_cert, ca_key = _make_ca_and_key()
    last = datetime.now(tz=UTC) - timedelta(days=30)
    expired_at = datetime.now(tz=UTC) - timedelta(days=1)
    crl_pem = _build_crl(ca_cert, ca_key, last_update=last, next_update=expired_at)
    # Should NOT raise — warns instead.
    result = _verify_crl(crl_pem, _pem(ca_cert))
    assert isinstance(result, x509.CertificateRevocationList)


def test_verify_crl_rejects_empty_ca_bundle() -> None:
    """An empty CA bundle is unloadable as PEM — any ValueError refusal is fine."""
    ca_cert, ca_key = _make_ca_and_key()
    crl_pem = _build_crl(ca_cert, ca_key)
    with pytest.raises(ValueError):
        _verify_crl(crl_pem, b"")


# ---------- _validate_pem_material ----------


def test_validate_pem_material_accepts_matching_pair() -> None:
    ca_cert, ca_key = _make_ca_and_key()
    leaf_cert, leaf_key = _make_leaf(ca_cert, ca_key)
    crl_pem = _build_crl(ca_cert, ca_key)
    _validate_pem_material(_pem(leaf_cert), _key_pem(leaf_key), _pem(ca_cert), crl_pem)


def test_validate_pem_material_rejects_mismatched_cert_and_key() -> None:
    """HIGH-11: cert + private key must form a matching pair."""
    ca_cert, ca_key = _make_ca_and_key()
    leaf_cert, _ = _make_leaf(ca_cert, ca_key)
    _, other_key = _make_leaf(ca_cert, ca_key)  # different key, same CA
    crl_pem = _build_crl(ca_cert, ca_key)
    with pytest.raises(ValueError, match="matching pair"):
        _validate_pem_material(
            _pem(leaf_cert), _key_pem(other_key), _pem(ca_cert), crl_pem
        )


def test_validate_pem_material_rejects_empty_ca_bundle() -> None:
    ca_cert, ca_key = _make_ca_and_key()
    leaf_cert, leaf_key = _make_leaf(ca_cert, ca_key)
    crl_pem = _build_crl(ca_cert, ca_key)
    with pytest.raises(ValueError):
        _validate_pem_material(_pem(leaf_cert), _key_pem(leaf_key), b"", crl_pem)


# ---------- clientcert_sha256 ----------


def test_clientcert_sha256_is_deterministic() -> None:
    der = b"\x30\x82\x00\x01"  # arbitrary bytes
    a = clientcert_sha256(der)
    b = clientcert_sha256(der)
    assert a == b
    assert len(a) == 64  # hex of SHA-256


def test_clientcert_sha256_differs_for_different_input() -> None:
    a = clientcert_sha256(b"\x00")
    b = clientcert_sha256(b"\x01")
    assert a != b
