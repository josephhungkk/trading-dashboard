from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


async def _call_sidecar(sidecar: Any, method: str, **kwargs: Any) -> Any:
    fn = getattr(sidecar, method, None)
    if fn is None:
        snake = "".join([f"_{c.lower()}" if c.isupper() else c for c in method]).lstrip("_")
        fn = getattr(sidecar, snake, None)
    if fn is None:
        raise AttributeError(f"sidecar missing {method}")
    return await fn(**kwargs)


class CryptoService:
    def __init__(self, db: AsyncSession, redis: Any, sidecar: Any) -> None:
        self._db = db
        self._redis = redis
        self._sidecar = sidecar

    async def list_assets(self, account_id: str) -> list[dict[str, Any]]:
        cache_key = f"crypto:assets:{account_id}"
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)  # type: ignore[no-any-return]
        resp = await _call_sidecar(self._sidecar, "ListCryptoAssets", account_id=account_id)
        assets: list[dict[str, Any]] = []
        for asset in resp.assets:
            meta: dict[str, Any] = {
                "asset_class": "CRYPTO",
                "base_asset": asset.base_asset,
                "quote_asset": asset.quote_asset,
                "min_qty": asset.min_qty,
                "qty_step": asset.qty_step,
                "min_notional": asset.min_notional or None,
            }
            canonical_id = f"{asset.base_asset}.{asset.quote_asset}"
            await self._db.execute(
                text(
                    """
                    INSERT INTO instruments (canonical_id, asset_class, meta)
                    VALUES (:cid, 'CRYPTO', CAST(:meta AS jsonb))
                    ON CONFLICT (canonical_id) DO UPDATE
                    SET meta = EXCLUDED.meta, asset_class = EXCLUDED.asset_class
                    """
                ),
                {"cid": canonical_id, "meta": json.dumps(meta)},
            )
            await self._db.commit()
            assets.append({"canonical_id": canonical_id, **meta})
        await self._redis.set(cache_key, json.dumps(assets), ex=300)
        return assets

    async def resolve_instrument(self, symbol: str) -> dict[str, Any] | None:
        result = await self._db.execute(
            text(
                "SELECT id, canonical_id, meta FROM instruments "
                "WHERE canonical_id = :sym AND asset_class = :ac LIMIT 1"
            ),
            {"sym": symbol, "ac": "CRYPTO"},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None
