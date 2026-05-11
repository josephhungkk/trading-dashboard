"""Phase 10a D8 — RiskLimit CRUD + 60s cache + Redis pub/sub invalidation.

Pattern mirrors OrderCapabilityService:
- Per-process TTL cache (60s) on the full list_all() result; risk_limits
  has a small cardinality (~10-50 rows) so we cache the whole set
  rather than per-key.
- publish_invalidation() emits on the spec-mandated channel
  ``app_config:invalidate:risk_limits``; admin writes call it on every
  mutation so other workers' caches drop on the next read.
- Multi-worker pubsub listener integration is deferred to Phase 24
  (single uvicorn worker today), but the invalidation hook is in place.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

RISK_LIMITS_INVALIDATION_CHANNEL = "app_config:invalidate:risk_limits"
_CACHE_TTL_SECONDS = 60.0

_SessionFactory = Callable[[], Any]


class RedisLike(Protocol):
    async def publish(self, channel: str, message: bytes | str) -> int: ...


class RiskLimitsService:
    """CRUD for risk_limits with TTL cache + pubsub invalidation."""

    def __init__(
        self,
        redis: RedisLike,
        *,
        db: AsyncSession | None = None,
        db_factory: _SessionFactory | None = None,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if db is None and db_factory is None:
            raise ValueError("RiskLimitsService requires either db or db_factory")
        self._db = db
        self._db_factory = db_factory
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._now = now
        self._cache: tuple[list[dict[str, Any]], float] | None = None

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession]:
        if self._db is not None:
            yield self._db
        else:
            assert self._db_factory is not None
            async with self._db_factory() as session:
                yield session

    def invalidate(self) -> None:
        """Drop the in-process cache; safe to call from pubsub listener."""
        self._cache = None

    async def publish_invalidation(self) -> None:
        """Drop local cache + tell peer workers via Redis pubsub."""
        self.invalidate()
        try:
            await self._redis.publish(RISK_LIMITS_INVALIDATION_CHANNEL, b"")
        except (ConnectionError, OSError, TimeoutError) as exc:
            log.warning("risk_limits invalidation publish failed: err=%s", exc)

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every risk_limits row; cached for 60s."""
        if self._cache is not None:
            rows, deadline = self._cache
            if self._now() < deadline:
                return rows
        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    SELECT id, scope_type::text AS scope_type, scope_id,
                           limit_kind::text AS limit_kind, limit_value,
                           warn_at_pct, is_active, notes,
                           created_at, updated_at, updated_by
                      FROM risk_limits
                     ORDER BY scope_type, scope_id NULLS FIRST, limit_kind
                    """
                )
            )
            rows = [dict(row) for row in result.mappings().all()]
        self._cache = (rows, self._now() + self._ttl_seconds)
        return rows

    async def create(
        self,
        *,
        scope_type: str,
        scope_id: str | None,
        limit_kind: str,
        limit_value: Decimal,
        warn_at_pct: Decimal | None,
        is_active: bool,
        notes: str,
        updated_by: str,
    ) -> dict[str, Any]:
        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    INSERT INTO risk_limits
                        (scope_type, scope_id, limit_kind, limit_value,
                         warn_at_pct, is_active, notes, updated_by)
                    VALUES
                        (CAST(:scope_type AS risk_scope_type), :scope_id,
                         CAST(:limit_kind AS risk_limit_kind), :limit_value,
                         :warn_at_pct, :is_active, :notes, :updated_by)
                    RETURNING id, scope_type::text AS scope_type, scope_id,
                              limit_kind::text AS limit_kind, limit_value,
                              warn_at_pct, is_active, notes,
                              created_at, updated_at, updated_by
                    """
                ),
                {
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "limit_kind": limit_kind,
                    "limit_value": limit_value,
                    "warn_at_pct": warn_at_pct,
                    "is_active": is_active,
                    "notes": notes,
                    "updated_by": updated_by,
                },
            )
            row = dict(result.mappings().one())
            await db.commit()
        await self.publish_invalidation()
        return row

    async def update(
        self,
        limit_id: int,
        *,
        scope_type: str,
        scope_id: str | None,
        limit_kind: str,
        limit_value: Decimal,
        warn_at_pct: Decimal | None,
        is_active: bool,
        notes: str,
        updated_by: str,
    ) -> dict[str, Any] | None:
        """Returns the updated row or None if the id doesn't exist.

        The BEFORE UPDATE trigger fn_risk_limits_history snapshots the OLD
        row into risk_limits_history with changed_by = NEW.updated_by.
        """
        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    UPDATE risk_limits
                       SET scope_type = CAST(:scope_type AS risk_scope_type),
                           scope_id = :scope_id,
                           limit_kind = CAST(:limit_kind AS risk_limit_kind),
                           limit_value = :limit_value,
                           warn_at_pct = :warn_at_pct,
                           is_active = :is_active,
                           notes = :notes,
                           updated_at = now(),
                           updated_by = :updated_by
                     WHERE id = :id
                    RETURNING id, scope_type::text AS scope_type, scope_id,
                              limit_kind::text AS limit_kind, limit_value,
                              warn_at_pct, is_active, notes,
                              created_at, updated_at, updated_by
                    """
                ),
                {
                    "id": limit_id,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "limit_kind": limit_kind,
                    "limit_value": limit_value,
                    "warn_at_pct": warn_at_pct,
                    "is_active": is_active,
                    "notes": notes,
                    "updated_by": updated_by,
                },
            )
            row = result.mappings().one_or_none()
            await db.commit()
        if row is None:
            return None
        await self.publish_invalidation()
        return dict(row)

    async def delete(self, limit_id: int) -> bool:
        async with self._session() as db:
            result = await db.execute(
                text("DELETE FROM risk_limits WHERE id = :id RETURNING id"),
                {"id": limit_id},
            )
            deleted_row = result.scalar_one_or_none()
            await db.commit()
        if deleted_row is None:
            return False
        await self.publish_invalidation()
        return True
