"""Phase 10a — Redis-backed in-flight counters for risk gate optimism.

Closes the broker-staleness window between order submit and broker ACK by
predicting consumption optimistically (decrement at submit; revert on
rejection; reconcile to broker-reported on each discoverer poll).

Spec: ``docs/superpowers/specs/2026-05-08-phase10a-risk-engine-design.md``
§1 #4 (PDT, H1) and §1 #6 (buying-power, H3).

Single-replica today — concurrent ``decrement`` from multiple uvicorn workers
can race; Phase 24 introduces multi-worker locking before scaling out.
``reconcile_*`` writes carry a 120s TTL so a discoverer outage cannot leave
a stale counter pinned past two poll cycles.
"""

from __future__ import annotations

import uuid
from typing import Any


def _pdt_key(account_id: uuid.UUID) -> str:
    return f"risk:pdt:{account_id}"


def _bp_key(account_id: uuid.UUID) -> str:
    return f"risk:bp_committed:{account_id}"


# ─── PDT counter ────────────────────────────────────────────────────────


async def decrement_pdt(redis: Any, account_id: uuid.UUID) -> int:
    """Optimistically decrement remaining day-trade count; return new value."""
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


async def commit_bp(redis: Any, account_id: uuid.UUID, notional: float) -> float:
    """Add ``notional`` to in-flight committed-BP; return post-commit total."""
    return float(await redis.incrbyfloat(_bp_key(account_id), notional))


async def revert_bp(redis: Any, account_id: uuid.UUID, notional: float) -> float:
    """Subtract ``notional`` (cancel/reject path); return post-revert total."""
    return float(await redis.incrbyfloat(_bp_key(account_id), -notional))


async def inflight_bp_committed(redis: Any, account_id: uuid.UUID) -> float:
    """Read in-flight committed BP total; ``0.0`` when unset."""
    raw = await redis.get(_bp_key(account_id))
    return float(raw) if raw is not None else 0.0


async def reconcile_bp_committed(redis: Any, account_id: uuid.UUID, broker_reported: float) -> None:
    """Overwrite in-flight BP committed with the authoritative broker value."""
    await redis.set(_bp_key(account_id), str(broker_reported), ex=120)
