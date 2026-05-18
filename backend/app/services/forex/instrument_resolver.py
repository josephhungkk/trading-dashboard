"""Phase 15a — read-only FX instrument resolver with Redis cache."""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
_CACHE_TTL = 3600  # 60 minutes


class ForexInstrumentResolver:
    """Read-only: resolves (base_currency, quote_currency) → instruments row dict.

    Does NOT write. Use _ensure_forex_instrument() in rfq_service.py for upsert.
    Returns None if the instrument row does not exist yet.
    """

    def __init__(self, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    def _cache_key(self, base: str, quote: str) -> str:
        return f"forex:instrument:{base}{quote}"

    async def resolve(self, base: str, quote: str) -> dict[str, Any] | None:
        key = self._cache_key(base, quote)
        cached = await self._redis.get(key)
        if cached is not None:
            return json.loads(cached)
        result = await self._db.execute(
            text(
                "SELECT id, canonical_id, conid, asset_class, meta "
                "FROM instruments WHERE asset_class = 'FOREX' "
                "AND meta->>'base_currency' = :base AND meta->>'quote_currency' = :quote "
                "LIMIT 1"
            ),
            {"base": base, "quote": quote},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        data = dict(row)
        await self._redis.set(key, json.dumps(data, default=str), ex=_CACHE_TTL)
        return data

    async def invalidate(self, base: str, quote: str) -> None:
        await self._redis.delete(self._cache_key(base, quote))
