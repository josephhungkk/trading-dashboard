"""Phase 7a OAuth helpers - state nonce, PG advisory lock, token-mint.

Architectural invariants:
  - H1: state nonce is HMAC-SHA256-signed; Redis stores raw nonce; SET NX
    atomic, GETDEL consume (single-use).
  - C2: backend is sole writer of schwab.refresh_token. PG advisory lock
    serializes Tier-1 vs Tier-2 vs sidecar near-expiry refreshes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

import structlog

log = structlog.get_logger(module="services.schwab_oauth")

_STATE_NONCE_PREFIX = "schwab_oauth_nonce:"
_STATE_NONCE_TTL_SEC = 600  # 10 minutes

# PG advisory lock id - derived from sha256("schwab.refresh_token")[0:4]
# truncated to a positive int32.
SCHWAB_REFRESH_LOCK_ID = (
    int.from_bytes(
        hashlib.sha256(b"schwab.refresh_token").digest()[:4],
        byteorder="big",
    )
    & 0x7FFFFFFF
)


class StateNonceError(Exception):
    pass


async def mint_state_nonce(
    redis: Any,
    *,
    user_email: str,
    app_secret_key: bytes,
) -> str:
    """Generate HMAC-signed nonce. Returns the signed value.

    Stored in Redis at SET key=schwab_oauth_nonce:{nonce} value={user_email}
    with NX (atomic check-and-set) + EX 600.
    """
    nonce = secrets.token_urlsafe(32)
    sig = hmac.new(app_secret_key, nonce.encode(), hashlib.sha256).digest()
    signed = f"{nonce}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"
    ok = await redis.set(
        f"{_STATE_NONCE_PREFIX}{nonce}",
        user_email,
        nx=True,
        ex=_STATE_NONCE_TTL_SEC,
    )
    if not ok:
        raise StateNonceError("nonce collision (extremely rare)")
    return signed


async def consume_state_nonce(
    redis: Any,
    *,
    signed: str,
    app_secret_key: bytes,
) -> str:
    """Validate HMAC + atomically consume from Redis. Returns user_email.

    Raises StateNonceError on any failure path.
    """
    if "." not in signed:
        raise StateNonceError("malformed state value")
    nonce, sig_b64 = signed.rsplit(".", 1)
    expected = hmac.new(app_secret_key, nonce.encode(), hashlib.sha256).digest()
    given_sig = _b64_decode_padded(sig_b64)
    if not hmac.compare_digest(expected, given_sig):
        raise StateNonceError("invalid signature")
    # GETDEL - atomic single-use consume (Redis 6.2+).
    user_email: object = await redis.execute_command(
        "GETDEL",
        f"{_STATE_NONCE_PREFIX}{nonce}",
    )
    if user_email is None:
        raise StateNonceError("state nonce not found or consumed already")
    if isinstance(user_email, bytes):
        return user_email.decode()
    if isinstance(user_email, str):
        return user_email
    raise StateNonceError("state nonce value has unexpected type")


def _b64_decode_padded(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)
