from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_REDIS_TTL = 24 * 3600  # 24h


class BotConidUnresolvedError(Exception):
    pass


class BotConidResolver:
    """Resolves canonical_id → conid for a given broker.

    Resolution chain:
    1. symbol_aliases lookup (canonical_id → instrument_id)
    2. positions table lookup (instrument_id, account_id → conid)
    3. Redis cache (broker_id, instrument_id)
    4. sidecar GetContract RPC → cache 24h
    5. Fail: BotConidUnresolvedError
    """

    def __init__(self, db: AsyncSession, redis: Any, registry: Any) -> None:
        self._db = db
        self._redis = redis
        self._registry = registry

    async def resolve(
        self,
        canonical_id: str,
        broker_id: str,
        account_id: UUID,
    ) -> int:
        alias_row = await self._db.execute(
            text("SELECT instrument_id FROM symbol_aliases WHERE canonical_id = :cid LIMIT 1"),
            {"cid": canonical_id},
        )
        instrument_id = alias_row.scalar_one_or_none()
        if instrument_id is None:
            raise BotConidUnresolvedError(
                f"canonical_id {canonical_id!r} not found in symbol_aliases"
            )

        pos_row = await self._db.execute(
            text(
                """
                SELECT conid FROM positions
                WHERE account_id = :aid AND instrument_id = :iid
                ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"aid": account_id, "iid": instrument_id},
        )
        conid = pos_row.scalar_one_or_none()
        if conid is not None:
            return int(conid)

        cache_key = f"bot:conid:{broker_id}:{instrument_id}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return int(cached)

        try:
            broker = self._registry.get(broker_id)
            result = await broker.stub.GetContract(
                type("GetContractRequest", (), {"instrument_id": instrument_id})()
            )
            conid = int(result.conid)
            await self._redis.setex(cache_key, _REDIS_TTL, str(conid))
            return conid
        except Exception as exc:
            logger.warning(
                "conid_resolution_sidecar_failed",
                canonical_id=canonical_id,
                broker_id=broker_id,
                error=str(exc),
            )
            raise BotConidUnresolvedError(
                f"sidecar GetContract failed for canonical_id={canonical_id!r}: {exc}"
            ) from exc
