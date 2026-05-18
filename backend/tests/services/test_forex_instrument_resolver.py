"""Phase 15a: ForexInstrumentResolver tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.no_db, pytest.mark.asyncio]


def _make_db(row=None):
    db = AsyncMock()
    mappings_result = MagicMock()
    mappings_result.one_or_none.return_value = dict(row) if row else None
    execute_result = MagicMock()
    execute_result.mappings.return_value = mappings_result
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_redis(cached_json=None):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_json.encode() if cached_json else None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


async def test_resolver_returns_none_on_miss():
    from app.services.forex.instrument_resolver import ForexInstrumentResolver

    db = _make_db(row=None)
    redis = _make_redis(cached_json=None)
    resolver = ForexInstrumentResolver(db, redis)
    result = await resolver.resolve("EUR", "USD")
    assert result is None


async def test_resolver_returns_cached():
    from app.services.forex.instrument_resolver import ForexInstrumentResolver

    cached = json.dumps({"id": 42, "canonical_id": "EUR.USD"})
    redis = _make_redis(cached_json=cached)
    db = AsyncMock()  # should not be called
    resolver = ForexInstrumentResolver(db, redis)
    result = await resolver.resolve("EUR", "USD")
    assert result["id"] == 42
    db.execute.assert_not_called()


async def test_resolver_invalidate_cache():
    from app.services.forex.instrument_resolver import ForexInstrumentResolver

    redis = _make_redis()
    resolver = ForexInstrumentResolver(None, redis)
    await resolver.invalidate("EUR", "USD")
    redis.delete.assert_called_once_with("forex:instrument:EURUSD")
