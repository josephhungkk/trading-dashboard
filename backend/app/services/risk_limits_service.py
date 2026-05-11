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
import json
import time
from collections.abc import AsyncGenerator, Callable
from decimal import Decimal
from typing import Any, ClassVar, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

RISK_LIMITS_INVALIDATION_CHANNEL = "app_config:invalidate:risk_limits"
_CACHE_TTL_SECONDS = 60.0

_SessionFactory = Callable[[], Any]


class RedisLike(Protocol):
    async def publish(self, channel: str, message: bytes | str) -> int: ...


class RiskLimitsService:
    """CRUD for risk_limits with TTL cache + pubsub invalidation.

    D9-fix: the cache is class-level so it survives across the per-request
    instances FastAPI creates via dependency injection (the original
    per-instance cache never hit). Single uvicorn worker today (Phase 24
    multi-worker concern); a peer-worker pubsub listener will replace the
    blunt invalidate-on-mutate model when that ships.
    """

    _cache: ClassVar[tuple[list[dict[str, Any]], float] | None] = None

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

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession]:
        if self._db is not None:
            yield self._db
        else:
            if self._db_factory is None:
                raise RuntimeError("RiskLimitsService: neither db nor db_factory available")
            async with self._db_factory() as session:
                yield session

    @classmethod
    def invalidate(cls) -> None:
        """Drop the class-level cache; safe to call from pubsub listener."""
        cls._cache = None

    async def publish_invalidation(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        """Drop local cache + tell peer workers via Redis pubsub.

        D9-fix: payload now carries {scope_type, scope_id} per spec §4 so
        peer workers can do scoped cache invalidation when the Phase 24
        listener arrives. None values omitted; an unscoped publish
        (no kwargs) emits an empty object meaning "blanket-drop".
        """
        self.invalidate()
        payload: dict[str, Any] = {}
        if scope_type is not None:
            payload["scope_type"] = scope_type
        if scope_id is not None:
            payload["scope_id"] = scope_id
        try:
            await self._redis.publish(
                RISK_LIMITS_INVALIDATION_CHANNEL,
                json.dumps(payload).encode(),
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            log.warning("risk_limits.invalidation_publish_failed", err=str(exc))

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every risk_limits row; cached for 60s at class level."""
        cache = type(self)._cache
        if cache is not None:
            rows, deadline = cache
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
        type(self)._cache = (rows, self._now() + self._ttl_seconds)
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
        await self.publish_invalidation(scope_type=scope_type, scope_id=scope_id)
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
            if row is None:
                # D9-fix: skip the commit + invalidation on no-match;
                # the outer-tx fixture rolls back on context exit either way.
                return None
            await db.commit()
        await self.publish_invalidation(scope_type=scope_type, scope_id=scope_id)
        return dict(row)

    async def delete(self, limit_id: int, *, updated_by: str) -> dict[str, Any] | None:
        """Soft-delete: flip is_active=false and stamp updated_by/updated_at.

        D9-fix (spec §6): the endpoint is documented as "soft-delete
        (is_active=false), idempotent". A hard DELETE leaves no
        risk_limits_history row (the BEFORE UPDATE trigger doesn't fire
        on DELETE) and no record of who removed the limit. Returning the
        post-flip row lets the API decide whether to surface 204 (always)
        or echo the deactivated row.

        Returns None only when no row with that id exists; callers map
        to 404. Already-inactive rows still UPDATE (idempotent per spec).
        """
        async with self._session() as db:
            result = await db.execute(
                text(
                    """
                    UPDATE risk_limits
                       SET is_active = false,
                           updated_at = now(),
                           updated_by = :updated_by
                     WHERE id = :id
                    RETURNING id, scope_type::text AS scope_type, scope_id,
                              limit_kind::text AS limit_kind, limit_value,
                              warn_at_pct, is_active, notes,
                              created_at, updated_at, updated_by
                    """
                ),
                {"id": limit_id, "updated_by": updated_by},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            await db.commit()
        out = dict(row)
        await self.publish_invalidation(
            scope_type=out["scope_type"],
            scope_id=out["scope_id"],
        )
        return out
