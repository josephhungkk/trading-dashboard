from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL_SECONDS = 300


class CFDSearchService:
    _sf_locks: ClassVar[dict[str, asyncio.Lock]] = {}
    _sf_lock_meta = asyncio.Lock()

    def __init__(self, *, redis: Any, db: AsyncSession) -> None:
        self._redis = redis
        self._db = db

    @classmethod
    async def _sf_lock(cls, key: str) -> asyncio.Lock:
        async with cls._sf_lock_meta:
            if key not in cls._sf_locks:
                cls._sf_locks[key] = asyncio.Lock()
            return cls._sf_locks[key]

    @staticmethod
    def _loads(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return cast(list[dict[str, Any]], json.loads(raw))

    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized = query.lower()
        cache_key = f"cfd:search:{normalized}"
        cached = await self._redis.get(cache_key)
        if cached:
            return self._loads(cached)

        lock = await self._sf_lock(cache_key)
        async with lock:
            cached = await self._redis.get(cache_key)
            if cached:
                return self._loads(cached)

            result = await self._db.execute(
                text(
                    """
                    SELECT id, canonical_id, display_name, currency, primary_exchange, meta
                      FROM instruments
                     WHERE asset_class = 'CFD'
                       AND (
                            display_name ILIKE :q
                         OR canonical_id ILIKE :q
                         OR meta->>'underlying_symbol' ILIKE :q
                       )
                     ORDER BY display_name
                     LIMIT :lim
                    """
                ),
                {"q": f"%{query}%", "lim": limit},
            )
            rows = []
            for row in result.mappings().all():
                r = dict(row)
                meta = r.get("meta")
                if isinstance(meta, str):
                    r["meta"] = json.loads(meta)
                rows.append(r)
            await self._redis.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(rows, default=str))
            return rows

    async def get_by_id(self, instrument_id: int) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(
                """
                SELECT id, canonical_id, display_name, currency, primary_exchange, meta
                  FROM instruments
                 WHERE id = :id AND asset_class = 'CFD'
                 LIMIT 1
                """
            ),
            {"id": instrument_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        r = dict(row)
        meta = r.get("meta")
        if isinstance(meta, str):
            r["meta"] = json.loads(meta)
        return r
