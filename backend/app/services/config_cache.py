"""Per-worker in-memory cache + Redis pub/sub listener for invalidation."""

import asyncio
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.core import metrics

log = logging.getLogger(__name__)


class ConfigCache:
    def __init__(
        self,
        redis: Redis,
        channel: str,
        kind_label: str,
        ttl_seconds: int = 300,
    ) -> None:
        self.redis = redis
        self.channel = channel
        self.kind_label = kind_label
        self.ttl_seconds = ttl_seconds
        self._store: dict[tuple[str, str], tuple[Any, float]] = {}

    def get(self, key: tuple[str, str]) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if self.ttl_seconds <= 0 or (time.monotonic() - ts) > self.ttl_seconds:
            self._store.pop(key, None)
            metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))
            return None
        return value

    def set(self, key: tuple[str, str], value: Any) -> None:
        self._store[key] = (value, time.monotonic())
        metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))

    def pop(self, key: tuple[str, str]) -> None:
        self._store.pop(key, None)
        metrics.config_cache_size.labels(kind=self.kind_label).set(len(self._store))

    async def publish_invalidation(self, namespace: str, key: str) -> None:
        payload = f"{namespace}|{key}".encode()
        try:
            await self.redis.publish(self.channel, payload)
        except Exception as e:
            log.warning(
                "config_cache publish failed: channel=%s ns=%s key=%s err=%s",
                self.channel,
                namespace,
                key,
                e,
            )
            metrics.redis_publish_fail_total.labels(channel=self.channel).inc()

    async def run_listener(self) -> None:
        attempt = 0
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.channel)
                    attempt = 0
                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            ns, key = msg["data"].decode().split("|", 1)
                            self.pop((ns, key))
                        except UnicodeDecodeError, ValueError:
                            log.warning("bad invalidation payload: %r", msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(
                    "config_cache listener disconnected: channel=%s attempt=%d err=%s",
                    self.channel,
                    attempt,
                    e,
                )
                metrics.redis_subscribe_reconnect_total.labels(channel=self.channel).inc()
                await asyncio.sleep(min(2**attempt, 30))
                attempt += 1
