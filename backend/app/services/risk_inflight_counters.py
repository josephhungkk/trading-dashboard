"""Phase 10a + 10a.5 — Redis-backed in-flight counters for risk gate optimism.

Closes the broker-staleness window between order submit and broker ACK by
predicting consumption optimistically (decrement at submit; revert on
rejection; reconcile to broker-reported on each discoverer poll).

Spec: ``docs/superpowers/specs/2026-05-11-phase10a5-cleanup-design.md`` §4
A4 widens the API to be **token-bearing**: ``decrement_pdt`` and ``commit_bp``
return a (value, token) tuple; ``revert_*`` and ``commit_*_finalize`` consume
the token via atomic Lua scripts so a double-revert is a no-op (HIGH-2).

Token contract
- Key shape: ``risk:pdt:tok:{account_id}:{uuid}`` / ``risk:bp:tok:{account_id}:{uuid}``
  The account_id prefix enables the discoverer's per-account orphan sweep to
  scope its SCAN MATCH to ``risk:pdt:tok:{account_id}:*`` and avoid touching
  in-flight tokens belonging to other accounts (CRIT-1).
- TTL: 86400s (matches counter TTL — crash-leak <= 1 trading session)
- Idempotency: atomic Lua GETDEL+INCR / GETDEL+INCRBYFLOAT.

Single-replica today — concurrent ``decrement`` from multiple uvicorn workers
can race; Phase 24 introduces multi-worker locking before scaling out.
``reconcile_*`` writes carry a 120s TTL so a discoverer outage cannot leave
a stale counter pinned past two poll cycles.

Money values use ``Decimal`` everywhere (CONVENTIONS.md). The Redis wire
format is the Decimal-stringified value; ``incrbyfloat`` accepts Decimal
or float at the Protocol boundary, but we always pass Decimal-safe strings.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

# 24-hour TTL on PDT counter + token writes - bounds crash-leak to one session.
_PDT_TTL_SEC = 86400
_BP_TTL_SEC = 86400


def _pdt_key(account_id: uuid.UUID) -> str:
    return f"risk:pdt:{account_id}"


def _bp_key(account_id: uuid.UUID) -> str:
    return f"risk:bp_committed:{account_id}"


def _pdt_token_key(account_id: uuid.UUID, token: str) -> str:
    """Token key embeds account_id so the discoverer sweep can scope by account."""
    return f"risk:pdt:tok:{account_id}:{token}"


def _bp_token_key(account_id: uuid.UUID, token: str) -> str:
    """Token key embeds account_id so the discoverer sweep can scope by account."""
    return f"risk:bp:tok:{account_id}:{token}"


# Lua: revert is GETDEL on token + INCR on counter when token still present.
# Token-present check makes a second revert a no-op (HIGH-2).
_REVERT_PDT_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
    return redis.call('INCR', KEYS[2])
end
return redis.call('GET', KEYS[2])
"""

# Lua: commit is GETDEL on token only — counter stays at its decremented value.
_COMMIT_PDT_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
end
return redis.call('GET', KEYS[2])
"""

# Lua: revert BP reads stored notional from the token key, deletes the token,
# then subtracts notional from the counter. Double-revert is a no-op because
# the token (and its stored notional) is gone after the first call.
_REVERT_BP_LUA = """
local notional = redis.call('GET', KEYS[1])
if notional then
    redis.call('DEL', KEYS[1])
    redis.call('INCRBYFLOAT', KEYS[2], '-' .. notional)
end
return redis.call('GET', KEYS[2]) or '0'
"""

_COMMIT_BP_LUA = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
end
return redis.call('GET', KEYS[2]) or '0'
"""


# ─── PDT counter ────────────────────────────────────────────────────────


async def decrement_pdt(
    redis: Any, account_id: uuid.UUID, *, broker_reported: int | None = None
) -> tuple[int, str]:
    """Optimistically decrement; return ``(new_value, token)``.

    On a cold cache (key absent), ``broker_reported`` seeds the counter via
    ``SET NX EX 86400`` before the DECR so we never start at the Redis default
    of -1. Caller should pass ``broker_reported`` from
    ``sidecar.get_account_summary().day_trades_remaining`` whenever possible.

    The returned ``token`` MUST be passed to ``revert_pdt`` or ``commit_pdt``
    to make the operation idempotent under broker-reject + retry.
    """
    token = uuid.uuid4().hex
    if broker_reported is not None:
        await redis.set(_pdt_key(account_id), str(broker_reported), ex=_PDT_TTL_SEC, nx=True)
    await redis.set(_pdt_token_key(account_id, token), "1", ex=_PDT_TTL_SEC)
    new_value = int(await redis.decr(_pdt_key(account_id)))
    return new_value, token


async def revert_pdt(redis: Any, account_id: uuid.UUID, token: str) -> int:
    """Roll back a prior ``decrement_pdt`` (broker rejected). Idempotent."""
    result = await redis.eval(
        _REVERT_PDT_LUA,
        2,
        _pdt_token_key(account_id, token),
        _pdt_key(account_id),
    )
    return int(result) if result is not None else 0


async def commit_pdt(redis: Any, account_id: uuid.UUID, token: str) -> None:
    """Mark the decrement as finalized (broker ACKed). Counter stays put."""
    await redis.eval(
        _COMMIT_PDT_LUA,
        2,
        _pdt_token_key(account_id, token),
        _pdt_key(account_id),
    )


async def inflight_pdt_remaining(redis: Any, account_id: uuid.UUID) -> int | None:
    """Read current in-flight remaining; ``None`` when unset (cold cache)."""
    raw = await redis.get(_pdt_key(account_id))
    return int(raw) if raw is not None else None


async def reconcile_pdt(redis: Any, account_id: uuid.UUID, broker_reported: int) -> None:
    """Overwrite the counter with the authoritative broker-reported value."""
    await redis.set(_pdt_key(account_id), str(broker_reported), ex=120)


# ─── Buying-power committed counter ─────────────────────────────────────


async def commit_bp(redis: Any, account_id: uuid.UUID, notional: Decimal) -> tuple[Decimal, str]:
    """Add ``notional`` to in-flight committed-BP; return ``(total, token)``.

    The token's value stores the committed notional so ``revert_bp`` can
    subtract the exact amount even if the caller no longer holds the value.
    """
    token = uuid.uuid4().hex
    await redis.set(_bp_token_key(account_id, token), str(notional), ex=_BP_TTL_SEC)
    # DB M-1: pass Decimal as string so Redis parses exact precision; float()
    # rounds at ~15 sig digits and creates persistent drift vs the str-encoded
    # token payload that revert_bp uses to subtract.
    new_total = Decimal(str(await redis.incrbyfloat(_bp_key(account_id), str(notional))))
    return new_total, token


async def revert_bp(redis: Any, account_id: uuid.UUID, token: str) -> Decimal:
    """Subtract the previously-committed notional. Idempotent under double-call."""
    result = await redis.eval(
        _REVERT_BP_LUA,
        2,
        _bp_token_key(account_id, token),
        _bp_key(account_id),
    )
    return Decimal(str(result))


async def commit_bp_finalize(redis: Any, account_id: uuid.UUID, token: str) -> None:
    """Mark the BP commit as finalized (broker ACKed). Counter stays put."""
    await redis.eval(
        _COMMIT_BP_LUA,
        2,
        _bp_token_key(account_id, token),
        _bp_key(account_id),
    )


async def inflight_bp_committed(redis: Any, account_id: uuid.UUID) -> Decimal:
    """Read in-flight committed BP total; ``Decimal('0')`` when unset."""
    raw = await redis.get(_bp_key(account_id))
    return Decimal(str(raw)) if raw is not None else Decimal("0")


async def reconcile_bp_committed(
    redis: Any, account_id: uuid.UUID, broker_reported: Decimal
) -> None:
    """Overwrite in-flight BP committed with the authoritative broker value."""
    await redis.set(_bp_key(account_id), str(broker_reported), ex=120)
