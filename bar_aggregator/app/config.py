"""Environment-backed settings for the bar aggregator service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str
    database_url: str
    flush_interval_ms: int = 1000
    aggregator_shard: int = 0
    aggregator_shard_count: int = 1
    http_port: int = 9100

    @classmethod
    def from_env(cls) -> Settings:
        redis_url = os.environ.get("REDIS_URL")
        database_url = os.environ.get("DATABASE_URL")

        missing = [
            name
            for name, value in (
                ("REDIS_URL", redis_url),
                ("DATABASE_URL", database_url),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        assert redis_url is not None
        assert database_url is not None

        return cls(
            redis_url=redis_url,
            database_url=database_url,
            flush_interval_ms=int(os.environ.get("FLUSH_INTERVAL_MS", "1000")),
            aggregator_shard=int(os.environ.get("AGGREGATOR_SHARD", "0")),
            aggregator_shard_count=int(os.environ.get("AGGREGATOR_SHARD_COUNT", "1")),
            http_port=int(os.environ.get("HTTP_PORT", "9100")),
        )
