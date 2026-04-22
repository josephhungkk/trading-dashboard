"""Crypto primitives for Fernet-encrypted secrets."""

import pytest
from cryptography.fernet import InvalidToken

from app.core.crypto import decrypt_bytes, derive_fernet_key, encrypt_bytes, get_fernet


def test_derive_is_deterministic():
    k1 = derive_fernet_key("my-secret-key-xyz")
    k2 = derive_fernet_key("my-secret-key-xyz")
    assert k1 == k2
    assert len(k1) == 44  # base64-encoded 32 bytes = 44 chars


def test_derive_differs_on_input_change():
    assert derive_fernet_key("a") != derive_fernet_key("b")


def test_encrypt_decrypt_roundtrip():
    fernet = get_fernet("test-key-123", None)
    plaintext = b"hello world"
    ct = encrypt_bytes(fernet, plaintext)
    assert ct != plaintext
    assert decrypt_bytes(fernet, ct) == plaintext


def test_encrypt_decrypt_empty_bytes():
    fernet = get_fernet("k", None)
    assert decrypt_bytes(fernet, encrypt_bytes(fernet, b"")) == b""


def test_encrypt_decrypt_large_blob():
    fernet = get_fernet("k", None)
    blob = b"x" * (1024 * 1024)  # 1 MB
    assert decrypt_bytes(fernet, encrypt_bytes(fernet, blob)) == blob


def test_decrypt_with_wrong_key_raises():
    f1 = get_fernet("key-a", None)
    f2 = get_fernet("key-b", None)
    ct = encrypt_bytes(f1, b"secret")
    with pytest.raises(InvalidToken):
        decrypt_bytes(f2, ct)


def test_multifernet_prev_key_fallback():
    """Data encrypted with the PREV key still decrypts when PRIMARY is new."""
    old_fernet = get_fernet("old-key", None)
    ct_old = encrypt_bytes(old_fernet, b"stored-under-old-key")

    rotated_fernet = get_fernet("new-key", "old-key")  # primary, prev
    assert decrypt_bytes(rotated_fernet, ct_old) == b"stored-under-old-key"

    ct_new = encrypt_bytes(rotated_fernet, b"fresh")
    assert decrypt_bytes(rotated_fernet, ct_new) == b"fresh"

    primary_only = get_fernet("new-key", None)
    with pytest.raises(InvalidToken):
        decrypt_bytes(primary_only, ct_old)
    assert decrypt_bytes(primary_only, ct_new) == b"fresh"


def test_get_fernet_none_prev_equals_single_fernet():
    """Passing None for prev should give a functional Fernet (not MultiFernet with empty)."""
    f = get_fernet("k", None)
    ct = encrypt_bytes(f, b"data")
    assert decrypt_bytes(f, ct) == b"data"
