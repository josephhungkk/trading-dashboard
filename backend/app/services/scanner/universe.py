from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from app.services.scanner.schemas import UniverseConfig

log = structlog.get_logger()


class UniverseResolver:
    def __init__(self, *, db: Any, cfg: Any, redis: Any) -> None:
        self._db = db
        self._cfg = cfg
        self._redis = redis

    async def resolve(self, config: UniverseConfig) -> list[str]:
        try:
            if config.type == "tickers":
                return list(config.params.get("tickers", []))
            if config.type == "watchlist":
                return await self._from_watchlist(config.params.get("watchlist_id"))
            if config.type == "instruments":
                return await self._all_instruments()
            if config.type == "schwab_screener":
                return await self._schwab_screener(config.params)
        except Exception:
            log.warning("scanner.universe.resolve_error", type=config.type)
        return []

    async def _all_instruments(self) -> list[str]:
        rows = await self._db.execute(
            sa.text("SELECT canonical_id FROM instruments WHERE canonical_id IS NOT NULL")
        )
        return [r.canonical_id for r in rows.fetchall()]

    async def _from_watchlist(self, watchlist_id: str | None) -> list[str]:
        if not watchlist_id:
            return []
        rows = await self._db.execute(
            sa.text(
                "SELECT i.canonical_id FROM watchlist_entries we "
                "JOIN instruments i ON i.id = we.instrument_id "
                "WHERE we.watchlist_id = :wid"
            ),
            {"wid": watchlist_id},
        )
        return [r.canonical_id for r in rows.fetchall()]

    async def _schwab_screener(self, params: dict) -> list[str]:
        log.info("scanner.universe.schwab_screener", params=params)
        return []
