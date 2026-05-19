from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from app.services.filings.hkex_rns import HkexRnsPoller
from app.services.filings.sec_edgar import SecEdgarPoller

log = structlog.get_logger()


class FilingsService:
    """Orchestrates filing pollers and serves REST queries."""

    def __init__(
        self,
        *,
        db_factory: Any,
        edgar_client: Any,
        ai_client: Any = None,
    ) -> None:
        self._db_factory = db_factory
        self._sec_poller = SecEdgarPoller(
            db_factory=db_factory,
            edgar_client=edgar_client,
            ai_client=ai_client,
        )
        self._hkex_poller = HkexRnsPoller(
            db_factory=db_factory,
            ai_client=ai_client,
        )

    async def poll_all(self) -> None:
        await self._sec_poller.poll()
        await self._hkex_poller.poll()

    async def list_filings(
        self,
        db: Any,
        *,
        canonical_id: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        filters = []
        params: dict[str, Any] = {"lim": limit, "off": offset}
        if canonical_id:
            filters.append("canonical_id = :cid")
            params["cid"] = canonical_id
        if source:
            filters.append("source = :src")
            params["src"] = source
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = await db.execute(
            sa.text(
                f"SELECT * FROM filings {where} ORDER BY filing_date DESC LIMIT :lim OFFSET :off"
            ),
            params,
        )
        return [dict(r._mapping) for r in rows.fetchall()]
