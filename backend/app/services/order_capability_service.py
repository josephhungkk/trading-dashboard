"""Order capability lookup with per-process cache and Redis invalidation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics

log = logging.getLogger(__name__)

KNOWN_BROKERS = frozenset({"ibkr", "futu", "schwab", "alpaca"})
ORDER_CAPABILITY_INVALIDATION_CHANNEL = "app_config:invalidate:order_capabilities"
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_SIZE = 2048

_CacheKey = tuple[str, str, str, str]
# ETF collapses into STOCK rows (capability matrix is keyed on the equity
# bucket; alembic 0018 CHECK constraint allows STOCK/CRYPTO/OPTION/FUTURE/
# FOREX/BOND and backfills existing rows with 'STOCK').
_ASSET_CLASS_BUCKET = {"STOCK": "STOCK", "ETF": "STOCK"}


class RedisLike(Protocol):
    async def publish(self, channel: str, message: bytes | str) -> int: ...

    def pubsub(self) -> Any: ...


class OrderCapabilityService:
    def __init__(
        self,
        db: AsyncSession,
        redis: RedisLike,
        *,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        max_cache_size: int = _CACHE_MAX_SIZE,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._max_cache_size = max_cache_size
        self._now = now
        self._cache: OrderedDict[_CacheKey, tuple[dict[str, Any] | None, float]] = OrderedDict()

    async def is_supported(
        self, broker_id: str, asset_class: str, order_type: str, time_in_force: str
    ) -> bool:
        if broker_id not in KNOWN_BROKERS:
            metrics.order_capability_check_total.labels(
                broker=broker_id, result="unknown_broker"
            ).inc()
            return False

        bucket = _ASSET_CLASS_BUCKET.get(asset_class, asset_class)
        row = await self._get_capability(broker_id, bucket, order_type, time_in_force)
        supported = bool(row is not None and row["is_supported"])
        metrics.order_capability_check_total.labels(
            broker=broker_id,
            result="supported" if supported else "unsupported",
        ).inc()
        return supported

    async def is_supported_3tuple_deprecated(
        self, broker_id: str, order_type: str, time_in_force: str
    ) -> bool:
        structlog.get_logger(__name__).warning(
            "order_capability.legacy_3tuple_call",
            broker_id=broker_id,
        )
        metrics.order_capability_legacy_3tuple_calls_total.labels(broker_id=broker_id).inc()
        return await self.is_supported(broker_id, "STOCK", order_type, time_in_force)

    async def get_notes(
        self, broker_id: str, asset_class: str, order_type: str, time_in_force: str
    ) -> str:
        if broker_id not in KNOWN_BROKERS:
            return ""
        bucket = _ASSET_CLASS_BUCKET.get(asset_class, asset_class)
        row = await self._get_capability(broker_id, bucket, order_type, time_in_force)
        if row is None:
            return ""
        return str(row.get("notes") or "")

    async def list_capabilities(
        self, broker_id: str, asset_class: str | None = None
    ) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
        if broker_id not in KNOWN_BROKERS:
            return []
        if asset_class is not None:
            result = await self._db.execute(
                text(
                    """
                    SELECT broker_id, asset_class, order_type, time_in_force,
                           is_supported AS supported, notes
                    FROM broker_order_capability
                    WHERE broker_id = :broker_id
                      AND asset_class = :asset_class
                    ORDER BY order_type, time_in_force
                    """
                ),
                {"broker_id": broker_id, "asset_class": asset_class},
            )
            return [dict(row) for row in result.mappings().all()]

        result = await self._db.execute(
            text(
                """
                SELECT broker_id, asset_class, order_type, time_in_force,
                       is_supported AS supported, notes
                FROM broker_order_capability
                WHERE broker_id = :broker_id
                ORDER BY asset_class, order_type, time_in_force
                """
            ),
            {"broker_id": broker_id},
        )
        rows = [dict(row) for row in result.mappings().all()]
        supported_asset_classes = {
            str(row["asset_class"]) for row in rows if bool(row.get("supported"))
        }
        if len(supported_asset_classes) <= 1:
            return rows

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["asset_class"]), []).append(row)
        return grouped

    def invalidate(self, broker_id: str) -> None:
        for key in list(self._cache):
            if key[0] == broker_id:
                self._cache.pop(key, None)

    async def publish_invalidation(self, broker_id: str) -> None:
        self.invalidate(broker_id)
        try:
            await self._redis.publish(ORDER_CAPABILITY_INVALIDATION_CHANNEL, broker_id.encode())
        except (ConnectionError, OSError, TimeoutError) as exc:
            log.warning(
                "order capability invalidation publish failed: broker=%s err=%s",
                broker_id,
                exc,
            )
            metrics.order_capability_pubsub_failures_total.inc()
            self.invalidate(broker_id)

    async def run_listener(self) -> None:
        attempt = 0
        while True:
            try:
                async with self._redis.pubsub() as pubsub:
                    await pubsub.subscribe(ORDER_CAPABILITY_INVALIDATION_CHANNEL)
                    attempt = 0
                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            broker_id = self._decode_message(msg["data"])
                        except UnicodeDecodeError:
                            log.warning(
                                "bad order capability invalidation payload: %r", msg["data"]
                            )
                            continue
                        self.invalidate(broker_id)
                        metrics.order_capability_pubsub_invalidations_total.inc()
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, TimeoutError) as exc:
                log.warning(
                    "order capability listener disconnected: channel=%s attempt=%d err=%s",
                    ORDER_CAPABILITY_INVALIDATION_CHANNEL,
                    attempt,
                    exc,
                )
                await asyncio.sleep(min(2**attempt, 30))
                attempt += 1

    async def _get_capability(
        self, broker_id: str, asset_class: str, order_type: str, time_in_force: str
    ) -> dict[str, Any] | None:
        key = (broker_id, asset_class, order_type, time_in_force)
        cached = self._get_cached(key)
        if cached is not _CACHE_MISS:
            metrics.order_capability_cache_hits_total.labels(broker=broker_id).inc()
            return cast(dict[str, Any] | None, cached)

        metrics.order_capability_cache_misses_total.labels(broker=broker_id).inc()
        row = await self._fetch_capability(broker_id, asset_class, order_type, time_in_force)
        self._set_cached(key, row)
        return row

    async def _fetch_capability(
        self, broker_id: str, asset_class: str, order_type: str, time_in_force: str
    ) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(
                """
                SELECT broker_id, asset_class, order_type, time_in_force, is_supported, notes
                FROM broker_order_capability
                WHERE broker_id = :broker_id
                  AND asset_class = :asset_class
                  AND order_type = :order_type
                  AND time_in_force = :time_in_force
                """
            ),
            {
                "broker_id": broker_id,
                "asset_class": asset_class,
                "order_type": order_type,
                "time_in_force": time_in_force,
            },
        )
        row = result.mappings().first()
        if row is None:
            return None
        return dict(cast(Mapping[str, Any], row))

    def _get_cached(self, key: _CacheKey) -> dict[str, Any] | None | object:
        entry = self._cache.get(key)
        if entry is None:
            return _CACHE_MISS
        row, timestamp = entry
        if self._ttl_seconds <= 0 or (self._now() - timestamp) > self._ttl_seconds:
            self._cache.pop(key, None)
            return _CACHE_MISS
        self._cache.move_to_end(key)
        return row

    def _set_cached(self, key: _CacheKey, row: dict[str, Any] | None) -> None:
        self._cache[key] = (row, self._now())
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_cache_size:
            evicted_key, _ = self._cache.popitem(last=False)
            metrics.order_capability_cache_evictions_total.labels(broker_id=evicted_key[0]).inc()

    @staticmethod
    def _decode_message(data: object) -> str:
        if isinstance(data, bytes):
            return data.decode()
        return str(data)


_CACHE_MISS = object()
