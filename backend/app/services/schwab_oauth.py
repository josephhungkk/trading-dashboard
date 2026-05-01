"""Phase 7a OAuth helpers - state nonce, PG advisory lock, token-mint.

Architectural invariants:
  - H1: state nonce is HMAC-SHA256-signed; Redis stores raw nonce; SET NX
    atomic, GETDEL consume (single-use).
  - C2: backend is sole writer of schwab.refresh_token. PG advisory lock
    serializes Tier-1 vs Tier-2 vs sidecar near-expiry refreshes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from sqlalchemy import text

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


@asynccontextmanager
async def schwab_refresh_lock(db_session: Any, *, timeout_sec: int = 5) -> AsyncIterator[None]:
    """Async context manager that acquires the PG advisory lock for the
    Schwab refresh-token write path. Used by BOTH the OAuth code-exchange
    path and the refresh-token-rotation path.
    """
    res = await db_session.execute(
        text("SELECT pg_try_advisory_lock(:id)"),
        {"id": SCHWAB_REFRESH_LOCK_ID},
    )
    locked = bool(res.scalar())
    if not locked:
        for _ in range(timeout_sec):
            await asyncio.sleep(1)
            res = await db_session.execute(
                text("SELECT pg_try_advisory_lock(:id)"),
                {"id": SCHWAB_REFRESH_LOCK_ID},
            )
            if res.scalar():
                locked = True
                break
        if not locked:
            raise RuntimeError("schwab refresh advisory lock contention timeout")
    try:
        yield
    finally:
        await db_session.execute(
            text("SELECT pg_advisory_unlock(:id)"),
            {"id": SCHWAB_REFRESH_LOCK_ID},
        )


async def _persist_tokens_under_lock(
    *,
    config_service: Any,
    access_token: str,
    refresh_token: str,
    issued_at: datetime,
    rotate_refresh_issued_at: bool,
) -> None:
    """Write the new (access, refresh) pair to app_secrets + app_config.

    Caller MUST hold the PG advisory lock - this helper does not acquire it.
    rotate_refresh_issued_at=True for the OAuth code-exchange path AND for
    refresh-rotations that returned a new refresh_token; False otherwise.
    """
    await config_service.set_secret("broker", "schwab.access_token", access_token, value_type="str")
    await config_service.set_secret(
        "broker", "schwab.refresh_token", refresh_token, value_type="str"
    )
    await config_service.set(
        "broker",
        "schwab.access_token_issued_at",
        issued_at.isoformat(),
        value_type="str",
    )
    if rotate_refresh_issued_at:
        await config_service.set(
            "broker",
            "schwab.refresh_token_issued_at",
            issued_at.isoformat(),
            value_type="str",
        )


async def refresh_with_lock(
    *,
    db_session: Any,
    config_service: Any,
    app_key: str,
    app_secret: str,
    refresh_token: str,
    timeout_sec: int = 5,
) -> tuple[str, str, datetime]:
    """Mint new tokens under PG advisory lock; write to app_secrets atomically.

    Returns (new_access_token, new_refresh_token, access_issued_at).
    Schwab rotates the refresh_token on every refresh - both must be persisted.
    """
    async with schwab_refresh_lock(db_session, timeout_sec=timeout_sec):
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.schwabapi.com/v1/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                auth=(app_key, app_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"schwab token endpoint {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        new_access = data["access_token"]
        new_refresh = data.get("refresh_token") or refresh_token
        rotated = new_refresh != refresh_token
        issued_at = datetime.now(timezone.utc)  # noqa: UP017
        await _persist_tokens_under_lock(
            config_service=config_service,
            access_token=new_access,
            refresh_token=new_refresh,
            issued_at=issued_at,
            rotate_refresh_issued_at=rotated,
        )
        return new_access, new_refresh, issued_at
