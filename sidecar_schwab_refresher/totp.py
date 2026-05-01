"""Wrapper for pyotp — current TOTP code from Base32 secret."""
from __future__ import annotations

import pyotp


class TOTPError(ValueError):
    pass


def current_totp(secret_base32: str) -> str:
    try:
        totp = pyotp.TOTP(secret_base32)
        return totp.now()
    except (ValueError, TypeError) as e:
        raise TOTPError(f"invalid TOTP secret: {e}") from e
