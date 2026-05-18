from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, ClassVar, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL_SECONDS = 300


class FundSearchService:
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
        cache_key = f"funds:search:{normalized}"
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
                     WHERE asset_class = 'MUTUAL_FUND'
                       AND (
                            display_name ILIKE :q
                         OR canonical_id ILIKE :q
                         OR meta->>'isin' ILIKE :q
                         OR meta->>'cusip' ILIKE :q
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

    async def get_nav_snapshot(self, instrument_id: int) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(
                """
                SELECT nav, nav_date, source, captured_at
                  FROM fund_nav_snapshots
                 WHERE instrument_id = :iid
                 ORDER BY captured_at DESC
                 LIMIT 1
                """
            ),
            {"iid": instrument_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        data = dict(row)
        return {
            "nav": str(data["nav"]),
            "nav_date": data["nav_date"].isoformat()
            if hasattr(data.get("nav_date"), "isoformat")
            else data.get("nav_date"),
            "source": data.get("source"),
            "captured_at": data["captured_at"].isoformat()
            if hasattr(data.get("captured_at"), "isoformat")
            else data.get("captured_at"),
        }

    async def upsert_nav_snapshot(
        self,
        instrument_id: int,
        nav: Decimal,
        nav_date: str,
        source: str,
    ) -> None:
        await self._db.execute(
            text(
                """
                INSERT INTO fund_nav_snapshots (instrument_id, nav, nav_date, source)
                VALUES (:iid, :nav, :nav_date, :source)
                """
            ),
            {"iid": instrument_id, "nav": nav, "nav_date": nav_date, "source": source},
        )
