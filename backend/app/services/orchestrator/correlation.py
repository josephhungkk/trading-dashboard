from __future__ import annotations

import json
import math
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_MIN_BARS = 10  # minimum bars required for reliable Pearson


class CorrelationService:
    """Computes Pearson correlation matrix over bars_1d for held instruments.

    Stores full symmetric matrix to Redis (TTL 86400s) and writes an audit
    snapshot to portfolio_correlation_snapshots.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def compute_and_store(
        self,
        account_id: UUID,
        instrument_ids: list[int],
        db: AsyncSession,
        window_days: int = 30,
    ) -> dict[str, dict[str, float]]:
        t0 = time.time()
        returns: dict[int, list[float]] = {}
        for iid in instrument_ids:
            rows = (
                await db.execute(
                    text(
                        "SELECT close FROM bars_1d"
                        " WHERE instrument_id = :iid"
                        " ORDER BY bar_date DESC"
                        " LIMIT :n"
                    ),
                    {"iid": iid, "n": window_days + 1},
                )
            ).all()
            closes = [float(r[0]) for r in reversed(rows) if r[0] is not None]
            if len(closes) < _MIN_BARS + 1:
                log.warning("correlation_insufficient_bars", instrument_id=iid, n=len(closes))
                continue
            log_rets = [
                math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0 and closes[i] > 0
            ]
            if len(log_rets) >= _MIN_BARS:
                returns[iid] = log_rets

        matrix: dict[str, dict[str, float]] = {}
        iids = list(returns.keys())
        for iid_i in iids:
            matrix[str(iid_i)] = {}
            for iid_j in iids:
                if iid_i == iid_j:
                    matrix[str(iid_i)][str(iid_j)] = 1.0
                else:
                    matrix[str(iid_i)][str(iid_j)] = _pearson(returns[iid_i], returns[iid_j])

        redis_key = f"portfolio:correlation:{account_id}"
        await self._redis.set(redis_key, json.dumps(matrix), ex=86400)

        m.orchestrator_correlation_matrix_age_seconds.labels(account_id=str(account_id)).set(0)

        try:
            await db.execute(
                text(
                    "INSERT INTO portfolio_correlation_snapshots"
                    " (account_id, instrument_ids, matrix_json, window_days)"
                    " VALUES (:acct, :iids, :mat::jsonb, :win)"
                ),
                {
                    "acct": account_id,
                    "iids": instrument_ids,
                    "mat": json.dumps(matrix),
                    "win": window_days,
                },
            )
            await db.commit()
        except Exception:
            log.exception("correlation_snapshot_write_failed", account_id=str(account_id))

        log.info(
            "correlation_computed",
            account_id=str(account_id),
            n_instruments=len(iids),
            elapsed_s=round(time.time() - t0, 3),
        )
        return matrix

    async def read_from_redis(self, account_id: UUID) -> dict[str, dict[str, float]] | None:
        raw = await self._redis.get(f"portfolio:correlation:{account_id}")
        if raw is None:
            return None
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)
