"""Phase 7a C2 - H1 state nonce: HMAC-signed, atomic SET NX EX, GETDEL consume."""

import pytest

from app.services.schwab_oauth import (
    StateNonceError,
    consume_state_nonce,
    mint_state_nonce,
)


@pytest.mark.asyncio
async def test_mint_then_consume_succeeds(redis):
    signed = await mint_state_nonce(
        redis,
        user_email="u@example.com",
        app_secret_key=b"K",
    )
    user = await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")
    assert user == "u@example.com"


@pytest.mark.asyncio
async def test_consume_replays_reject(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")
    with pytest.raises(StateNonceError, match=r"not found or consumed"):
        await consume_state_nonce(redis, signed=signed, app_secret_key=b"K")


@pytest.mark.asyncio
async def test_consume_wrong_hmac_rejects(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K")
    # Tamper a middle character of the signature (after the dot) so the
    # decoded HMAC bytes definitely differ. Tampering the *last* character
    # would silently no-op when it lands in the b64 trailing-2-bits region
    # that single-`=` padding discards (e.g. 'A' -> 'B' both encode the
    # same final byte for a 32-byte SHA-256 digest).
    nonce, sig_b64 = signed.rsplit(".", 1)
    flipped = "B" if sig_b64[5] != "B" else "C"
    tampered_sig = sig_b64[:5] + flipped + sig_b64[6:]
    tampered = f"{nonce}.{tampered_sig}"
    # Two paths: signature bytes don't match HMAC ("invalid signature"), or
    # the tampered character makes the b64 fragment unparseable
    # ("invalid signature encoding"). Either is acceptable here.
    with pytest.raises(StateNonceError, match=r"invalid signature"):
        await consume_state_nonce(redis, signed=tampered, app_secret_key=b"K")


@pytest.mark.asyncio
async def test_consume_wrong_secret_rejects(redis):
    signed = await mint_state_nonce(redis, user_email="u@x", app_secret_key=b"K1")
    with pytest.raises(StateNonceError):
        await consume_state_nonce(redis, signed=signed, app_secret_key=b"K2")


@pytest.mark.asyncio
async def test_collision_rejected_via_nx(redis):
    """Same nonce twice -> second SET NX fails (atomic)."""
    from app.services.schwab_oauth import _STATE_NONCE_PREFIX

    nonce = "fixed_nonce_for_test"
    redis_key = f"{_STATE_NONCE_PREFIX}{nonce}"
    await redis.set(redis_key, "first", nx=True, ex=600)
    second = await redis.set(redis_key, "second", nx=True, ex=600)
    assert second is None
