"""Fernet-based encryption for app_secrets.

Key is derived deterministically from APP_SECRET_KEY via HKDF-SHA256.
MultiFernet([primary, prev]) supports rolling rotation: new writes encrypt
with primary; reads fall back to prev if set.
"""

import base64

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_SALT = b"dashboard.v1"
_HKDF_INFO = b"app_secrets.fernet.v1"


def derive_fernet_key(app_secret_key: str) -> bytes:
    """Derive a 44-byte base64-encoded Fernet key from APP_SECRET_KEY via HKDF-SHA256.

    Deterministic for a given input; changes in salt/info produce a different key.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(app_secret_key.encode())
    return base64.urlsafe_b64encode(raw)


def get_fernet(app_secret_key: str, prev_key: str | None) -> Fernet | MultiFernet:
    """Return a Fernet (prev=None) or MultiFernet([primary, prev])."""
    primary = Fernet(derive_fernet_key(app_secret_key))
    if prev_key is None:
        return primary
    prev = Fernet(derive_fernet_key(prev_key))
    # MultiFernet: encrypt uses [0]; decrypt tries each in order.
    return MultiFernet([primary, prev])


def encrypt_bytes(fernet: Fernet | MultiFernet, plaintext: bytes) -> bytes:
    return fernet.encrypt(plaintext)


def decrypt_bytes(fernet: Fernet | MultiFernet, ciphertext: bytes) -> bytes:
    return fernet.decrypt(ciphertext)
