"""Algo capability lookup with Redis TTL cache and pub/sub invalidation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

KNOWN_BROKERS = frozenset({"ibkr", "futu", "schwab", "alpaca"})
ALGO_CAPABILITY_INVALIDATION_CHANNEL = "broker_algo_capability:invalidate"
_CACHE_TTL_SECONDS = 300
_CACHE_KEY_PREFIX = "algo_cap"
_SessionFactory = Callable[[], Any]
log = structlog.get_logger(__name__)


class AlgoCapabilityService:
    def __init__(
        self,
        redis: Any,
        *,
        db: AsyncSession | None = None,
        db_factory: _SessionFactory | None = None,
        ttl_seconds: int = _CACHE_TTL_SECONDS,
    ) -> None:
        if db is None and db_factory is None:
            raise ValueError("AlgoCapabilityService requires either db or db_factory")
        self._db = db
        self._db_factory = db_factory
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _cache_key(broker_id: str, asset_class: str) -> str:
        return f"{_CACHE_KEY_PREFIX}:{broker_id}:{asset_class}"

    async def get_strategies(self, broker_id: str, asset_class: str) -> list[dict[str, Any]]:
        if broker_id not in KNOWN_BROKERS:
            return []

        key = self._cache_key(broker_id, asset_class)
        cached = await self._redis.get(key)
        if cached:
            metrics.algo_capability_cache_hits_total.labels(broker_id=broker_id).inc()
            return json.loads(cached)

        metrics.algo_capability_cache_misses_total.labels(broker_id=broker_id).inc()
        if self._db is not None:
            rows = await self._fetch_strategies(self._db, broker_id, asset_class)
        else:
            assert self._db_factory is not None
            async with self._db_factory() as session:
                rows = await self._fetch_strategies(session, broker_id, asset_class)

        payload = json.dumps(rows)
        await self._redis.setex(key, self._ttl_seconds, payload)
        return rows

    async def _fetch_strategies(
        self, session: AsyncSession, broker_id: str, asset_class: str
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                """
                SELECT algo_strategy, enabled, notes
                FROM broker_algo_capability
                WHERE broker_id = :broker_id
                  AND asset_class = :asset_class
                  AND enabled = TRUE
                ORDER BY algo_strategy
                """
            ),
            {"broker_id": broker_id, "asset_class": asset_class},
        )
        rows = [dict(r._mapping) for r in result.fetchall()]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
            row["notes"] = str(row["notes"]) if row["notes"] is not None else None
        return rows

    async def _delete_matching_keys(self, pattern: str) -> None:
        async for key in self._scan_keys(pattern):
            await self._redis.delete(key)

    async def _scan_keys(self, pattern: str) -> AsyncGenerator[Any]:
        if hasattr(self._redis, "scan_iter"):
            async for key in self._redis.scan_iter(match=pattern):
                yield key
            return
        for key in await self._redis.keys(pattern):
            yield key

    async def _handle_invalidation(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            log.warning("algo_capability.invalidate_malformed", message=message)
            metrics.algo_capability_invalidate_malformed_total.inc()
            return
        except TypeError:
            log.warning("algo_capability.invalidate_malformed", message=message)
            metrics.algo_capability_invalidate_malformed_total.inc()
            return

        if not isinstance(payload, dict):
            log.warning("algo_capability.invalidate_malformed", payload=payload)
            metrics.algo_capability_invalidate_malformed_total.inc()
            return

        if "broker_id" in payload and "asset_class" in payload:
            key = self._cache_key(str(payload["broker_id"]), str(payload["asset_class"]))
            await self._redis.delete(key)
        elif "broker_id" in payload:
            await self._delete_matching_keys(f"{_CACHE_KEY_PREFIX}:{payload['broker_id']}:*")
        elif payload == {}:
            await self._delete_matching_keys(f"{_CACHE_KEY_PREFIX}:*")
        else:
            log.warning("algo_capability.invalidate_malformed", payload=payload)
            metrics.algo_capability_invalidate_malformed_total.inc()

    async def run_listener(self) -> None:
        # Mirrors OrderCapabilityService.run_listener: reconnect on transient Redis errors.
        attempt = 0
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(ALGO_CAPABILITY_INVALIDATION_CHANNEL)
                attempt = 0
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    try:
                        await self._handle_invalidation(data)
                    except Exception as exc:
                        log.exception("algo_capability.listener_error", exc_info=exc)
            except asyncio.CancelledError:
                await pubsub.unsubscribe(ALGO_CAPABILITY_INVALIDATION_CHANNEL)
                raise
            except (ConnectionError, OSError, TimeoutError) as exc:
                log.warning(
                    "algo_capability.listener_disconnected",
                    attempt=attempt,
                    err=str(exc),
                )
                await asyncio.sleep(min(2**attempt, 30))
                attempt += 1
            except Exception:
                log.exception("algo_capability.listener_failed")
