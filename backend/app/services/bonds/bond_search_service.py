from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, ClassVar, cast

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 300


class BondSearchService:
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

    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized_query = query.lower()
        cache_key = f"bonds:search:{normalized_query}"
        cached = await self._redis.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            return cast(list[dict[str, Any]], json.loads(cached))

        lock = await self._sf_lock(cache_key)
        async with lock:
            cached = await self._redis.get(cache_key)
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode("utf-8")
                return cast(list[dict[str, Any]], json.loads(cached))

            result = await self._db.execute(
                text(
                    """
                    SELECT id, canonical_id, display_name, currency, primary_exchange, meta
                      FROM instruments
                     WHERE asset_class = 'BOND'
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
            rows = result.mappings().all()
            items: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                meta = item.get("meta")
                if isinstance(meta, str):
                    try:
                        item["meta"] = json.loads(meta)
                    except json.JSONDecodeError:
                        log.warning("bond_search_meta_decode_failed", instrument_id=item.get("id"))
                items.append(item)

            await self._redis.setex(
                cache_key,
                _CACHE_TTL_SECONDS,
                json.dumps(items, default=str),
            )
            return items

    async def get_accrued_interest(self, instrument_id: int, account_id: str) -> Decimal | None:
        result = await self._db.execute(
            text(
                """
                SELECT accrued
                  FROM bonds_accrued_interest
                 WHERE instrument_id = :iid
                   AND account_id = :aid
                 ORDER BY as_of DESC
                 LIMIT 1
                """
            ),
            {"iid": instrument_id, "aid": account_id},
        )
        accrued = result.scalar_one_or_none()
        return Decimal(str(accrued)) if accrued is not None else None

    async def upsert_accrued_interest(
        self,
        instrument_id: int,
        account_id: str,
        accrued: Decimal,
        as_of: str,
    ) -> None:
        await self._db.execute(
            text(
                """
                INSERT INTO bonds_accrued_interest (instrument_id, account_id, accrued, as_of)
                VALUES (:iid, :aid, :accrued, :as_of)
                ON CONFLICT (instrument_id, account_id, as_of)
                DO UPDATE SET accrued = EXCLUDED.accrued
                """
            ),
            {"iid": instrument_id, "aid": account_id, "accrued": accrued, "as_of": as_of},
        )
