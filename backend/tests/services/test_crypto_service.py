from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_list_assets_uses_cache() -> None:
    from app.services.crypto.crypto_service import CryptoService

    redis = AsyncMock()
    redis.get.return_value = json.dumps([{"canonical_id": "BTC.USD", "asset_class": "CRYPTO"}])
    svc = CryptoService(db=AsyncMock(), redis=redis, sidecar=AsyncMock())
    result = await svc.list_assets("account-1")
    assert result[0]["canonical_id"] == "BTC.USD"
    redis.get.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_instrument_returns_none_when_missing() -> None:
    from app.services.crypto.crypto_service import CryptoService

    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = None
    db.execute.return_value = result
    svc = CryptoService(db=db, redis=AsyncMock(), sidecar=AsyncMock())
    result = await svc.resolve_instrument("UNKNOWN.USD")
    assert result is None
