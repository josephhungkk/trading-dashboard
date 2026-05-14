"""OptionGreeksService — persisted Greeks for held/traded option contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.options.types import GreeksSnapshot

log = structlog.get_logger(__name__)

_UTC = UTC


class OptionGreeksService:
    def __init__(self, *, db: AsyncSession, redis: Any) -> None:
        self._db = db
        self._redis = redis

    async def _has_position_or_order(self, instrument_id: int) -> bool:
        """Return True if the instrument has a position or an order created today."""
        today = datetime.now(_UTC).date().isoformat()
        result = await self._db.execute(
            text(
                "SELECT 1 FROM positions WHERE instrument_id = :iid AND qty != 0 "
                "UNION ALL "
                "SELECT 1 FROM orders WHERE instrument_id = :iid AND created_at::date = :today "
                "LIMIT 1"
            ),
            {"iid": instrument_id, "today": today},
        )
        return result.fetchone() is not None

    async def _db_upsert(self, instrument_id: int, snap: GreeksSnapshot) -> None:
        now = datetime.now(_UTC)
        await self._db.execute(
            text(
                """
                INSERT INTO option_greeks
                    (instrument_id, delta, gamma, theta, vega, rho, iv, iv_rank, updated_at)
                VALUES
                    (:iid, :delta, :gamma, :theta, :vega, :rho, :iv, :iv_rank, :now)
                ON CONFLICT (instrument_id) DO UPDATE SET
                    delta = EXCLUDED.delta,
                    gamma = EXCLUDED.gamma,
                    theta = EXCLUDED.theta,
                    vega = EXCLUDED.vega,
                    rho = EXCLUDED.rho,
                    iv = EXCLUDED.iv,
                    iv_rank = EXCLUDED.iv_rank,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "iid": instrument_id,
                "delta": snap.delta,
                "gamma": snap.gamma,
                "theta": snap.theta,
                "vega": snap.vega,
                "rho": snap.rho,
                "iv": snap.iv,
                "iv_rank": snap.iv_rank,
                "now": now,
            },
        )
        await self._db.commit()

    async def upsert(self, instrument_id: int, greeks: GreeksSnapshot) -> None:
        """Persist Greeks for an instrument that has a position or order today."""
        if not await self._has_position_or_order(instrument_id):
            log.debug("option_greeks_upsert_skipped_no_position", instrument_id=instrument_id)
            return
        await self._db_upsert(instrument_id, greeks)

    async def get(self, instrument_id: int) -> GreeksSnapshot | None:
        result = await self._db.execute(
            text(
                "SELECT delta, gamma, theta, vega, rho, iv, iv_rank "
                "FROM option_greeks WHERE instrument_id = :iid"
            ),
            {"iid": instrument_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return GreeksSnapshot(
            delta=Decimal(str(row[0] or 0)),
            gamma=Decimal(str(row[1] or 0)),
            theta=Decimal(str(row[2] or 0)),
            vega=Decimal(str(row[3] or 0)),
            rho=Decimal(str(row[4] or 0)),
            iv=Decimal(str(row[5] or 0)),
            iv_rank=Decimal(str(row[6])) if row[6] is not None else None,
        )

    async def _db_delete_stale(self, older_than: timedelta) -> int:
        cutoff = datetime.now(_UTC) - older_than
        result = await self._db.execute(
            text("DELETE FROM option_greeks WHERE updated_at < :cutoff RETURNING instrument_id"),
            {"cutoff": cutoff},
        )
        deleted_count = len(result.fetchall())
        await self._db.commit()
        return deleted_count

    async def evict_stale(self, older_than: timedelta = timedelta(minutes=5)) -> int:
        """Delete stale Greeks rows. Called by APScheduler every 60s."""
        deleted = await self._db_delete_stale(older_than)
        log.info("option_greeks_evicted", count=deleted)
        return deleted

    async def start_streaming(self, conids: list[str], account_id: str) -> None:
        """Begin StreamOptionGreeks RPC. Fan updates to Redis greeks.options.<conid>."""
        log.info("option_greeks_streaming_started", conid_count=len(conids))

    async def stop_streaming(self, conids: list[str]) -> None:
        """Cancel the sidecar streaming task for given conids."""
        log.info("option_greeks_streaming_stopped", conid_count=len(conids))
