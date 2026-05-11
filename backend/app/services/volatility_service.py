"""Phase 10b.1 — daily-bar realized-vol + ATR for vol-targeted sizing.

Lifespan singleton. Reads bars_1d (Phase 9). Redis-caches results at
``vol14:{instrument_id}:{asof_date}`` with TTL 6h. Returns None when
fewer than 15 closes exist (caller raises 422).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_KEY = "vol14:{instrument_id}:{asof_date}"
_CACHE_TTL_SECONDS = 6 * 60 * 60


class _RedisLike(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: str, *, ex: int) -> Any: ...


class _SessionFactory(Protocol):
    def __call__(self) -> Any: ...


@dataclass(frozen=True)
class VolatilityEstimate:
    """Realized-vol + ATR snapshot for one instrument-day. Decimal end-to-end."""

    realized_vol14_annualized: Decimal
    atr14: Decimal
    bars_used: int
    asof_date: date


class VolatilityService:
    """Singleton; constructed once in app.main.lifespan."""

    def __init__(self, db_factory: _SessionFactory, redis: _RedisLike) -> None:
        self._db_factory = db_factory
        self._redis = redis

    async def compute(
        self,
        instrument_id: UUID,
        asof_date: date,
    ) -> VolatilityEstimate | None:
        key = _CACHE_KEY.format(instrument_id=instrument_id, asof_date=asof_date.isoformat())
        cached = await self._redis.get(key)
        if cached is not None:
            return _decode_cached(cached)

        async with self._db_factory() as db:
            rows = await self._load_bars(db, instrument_id, asof_date)
        if len(rows) < 15:
            return None

        estimate = _compute_estimate(rows, asof_date)
        await self._redis.set(key, _encode(estimate), ex=_CACHE_TTL_SECONDS)
        return estimate

    async def _load_bars(
        self, db: AsyncSession, instrument_id: UUID, asof_date: date
    ) -> list[tuple[date, Decimal, Decimal, Decimal]]:
        """Return up to 15 most-recent (date, high, low, close) rows ending at asof_date."""
        stmt = text(
            """
            SELECT bar_date, high, low, close
            FROM bars_1d
            WHERE instrument_id = :iid AND bar_date <= :asof
            ORDER BY bar_date DESC
            LIMIT 15
            """
        )
        result = await db.execute(stmt, {"iid": instrument_id, "asof": asof_date})
        rows = list(result.all())
        rows.reverse()  # oldest first for stable iteration
        return [(r[0], Decimal(r[1]), Decimal(r[2]), Decimal(r[3])) for r in rows]


def _compute_estimate(
    rows: list[tuple[date, Decimal, Decimal, Decimal]],
    asof_date: date,
) -> VolatilityEstimate:
    closes = [r[3] for r in rows]
    # 14 log returns from 15 closes (oldest..newest order).
    log_returns: list[Decimal] = []
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev <= 0 or curr <= 0:
            raise ValueError(f"non-positive close in bars_1d: prev={prev} curr={curr}")
        log_returns.append(Decimal(math.log(float(curr / prev))))

    mean = sum(log_returns, Decimal(0)) / Decimal(len(log_returns))
    variance = sum((lr - mean) ** 2 for lr in log_returns) / Decimal(len(log_returns))
    daily_stddev = Decimal(math.sqrt(float(variance)))
    realized_vol_annualized = (daily_stddev * Decimal(math.sqrt(252))).quantize(Decimal("1e-8"))

    # ATR(14): SMA of true range over the last 14 bars.
    true_ranges: list[Decimal] = []
    for i in range(1, len(rows)):
        prev_close = rows[i - 1][3]
        high, low = rows[i][1], rows[i][2]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    atr14 = (sum(true_ranges, Decimal(0)) / Decimal(len(true_ranges))).quantize(Decimal("1e-8"))

    return VolatilityEstimate(
        realized_vol14_annualized=realized_vol_annualized,
        atr14=atr14,
        bars_used=14,
        asof_date=asof_date,
    )


def _encode(estimate: VolatilityEstimate) -> str:
    return json.dumps(
        {
            "realized_vol14_annualized": str(estimate.realized_vol14_annualized),
            "atr14": str(estimate.atr14),
            "bars_used": estimate.bars_used,
            "asof_date": estimate.asof_date.isoformat(),
        }
    )


def _decode_cached(raw: bytes) -> VolatilityEstimate:
    data = json.loads(raw)
    return VolatilityEstimate(
        realized_vol14_annualized=Decimal(data["realized_vol14_annualized"]),
        atr14=Decimal(data["atr14"]),
        bars_used=int(data["bars_used"]),
        asof_date=date.fromisoformat(data["asof_date"]),
    )
