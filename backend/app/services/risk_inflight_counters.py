"""Phase 10a — Redis-backed in-flight counters for risk gate optimism.

Closes the broker-staleness window between order submit and broker ACK by
predicting consumption optimistically (decrement at submit; revert on
rejection; reconcile to broker-reported on each discoverer poll).

Spec: ``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md``
§1 #4 (PDT, H1) and §1 #6 (buying-power, H3).

Single-replica today - concurrent ``decrement`` from multiple uvicorn workers
can race; Phase 24 introduces multi-worker locking before scaling out.
``reconcile_*`` writes carry a 120s TTL so a discoverer outage cannot leave
a stale counter pinned past two poll cycles. ``decrement_pdt`` writes a
24-hour TTL on the first SETNX so a crash between submit and reconcile
cannot leave a stale counter past the next trading session.

Money values use ``Decimal`` everywhere (CONVENTIONS.md). The Redis wire
format is the Decimal-stringified value; ``incrbyfloat`` accepts Decimal
or float at the Protocol boundary, but we always pass Decimal-safe strings.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

# 24-hour TTL on PDT counter writes - bounds crash-leak to one trading session.
_PDT_TTL_SEC = 86400


def _pdt_key(account_id: uuid.UUID) -> str:
    return f"risk:pdt:{account_id}"


def _bp_key(account_id: uuid.UUID) -> str:
    return f"risk:bp_committed:{account_id}"


# ─── PDT counter ────────────────────────────────────────────────────────


async def decrement_pdt(
    redis: Any, account_id: uuid.UUID, *, broker_reported: int | None = None
) -> int:
    """Optimistically decrement remaining day-trade count; return new value.

    On a cold cache (key absent), ``broker_reported`` seeds the counter via
    ``SET NX EX 86400`` before the DECR so we never start at the Redis default
    of -1 (which would make the gate falsely BLOCK every trade until the
    next reconcile poll). Caller should pass ``broker_reported`` from
    ``sidecar.get_account_summary().day_trades_remaining`` whenever possible.

    The TTL is set on the seed write only, since DECR preserves any existing
    TTL. This bounds the crash-leak window to 24 hours per the module
    docstring contract.
    """
    if broker_reported is not None:
        # SET NX = only seed if key absent; preserves an existing reconciled value.
        await redis.set(_pdt_key(account_id), str(broker_reported), ex=_PDT_TTL_SEC, nx=True)
    return int(await redis.decr(_pdt_key(account_id)))


async def revert_pdt(redis: Any, account_id: uuid.UUID) -> int:
    """Roll back a prior ``decrement_pdt`` (broker rejected the order)."""
    return int(await redis.incr(_pdt_key(account_id)))


async def inflight_pdt_remaining(redis: Any, account_id: uuid.UUID) -> int | None:
    """Read current in-flight remaining; ``None`` when unset (cold cache)."""
    raw = await redis.get(_pdt_key(account_id))
    return int(raw) if raw is not None else None


async def reconcile_pdt(redis: Any, account_id: uuid.UUID, broker_reported: int) -> None:
    """Overwrite the counter with the authoritative broker-reported value."""
    await redis.set(_pdt_key(account_id), str(broker_reported), ex=120)


# ─── Buying-power committed counter (Phase 10a B6 H3) ───────────────────


async def commit_bp(redis: Any, account_id: uuid.UUID, notional: Decimal) -> Decimal:
    """Add ``notional`` to in-flight committed-BP; return post-commit total."""
    return Decimal(str(await redis.incrbyfloat(_bp_key(account_id), float(notional))))


async def revert_bp(redis: Any, account_id: uuid.UUID, notional: Decimal) -> Decimal:
    """Subtract ``notional`` (cancel/reject path); return post-revert total."""
    return Decimal(str(await redis.incrbyfloat(_bp_key(account_id), float(-notional))))


async def inflight_bp_committed(redis: Any, account_id: uuid.UUID) -> Decimal:
    """Read in-flight committed BP total; ``Decimal('0')`` when unset."""
    raw = await redis.get(_bp_key(account_id))
    return Decimal(str(raw)) if raw is not None else Decimal("0")


async def reconcile_bp_committed(
    redis: Any, account_id: uuid.UUID, broker_reported: Decimal
) -> None:
    """Overwrite in-flight BP committed with the authoritative broker value."""
    await redis.set(_bp_key(account_id), str(broker_reported), ex=120)
