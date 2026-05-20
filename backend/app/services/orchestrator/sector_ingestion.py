"""Sector ingestion from IBKR GetContractFundamentals.

Equity/ETF/Index/Warrant → IBKR gRPC path.
Non-equity → synthetic: sector = '_class:{asset_class.lower()}'.
All values normalised: strip().lower().
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_EQUITY_CLASSES = {"STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "OPTION"}
_SYNTHETIC_CLASSES = {"FOREX", "CRYPTO", "FUTURE", "BOND", "MUTUAL_FUND", "CFD"}
_IBKR_BATCH_DELAY_S = 0.1  # 100ms between requests for IBKR pacing


class SectorIngestionService:
    def __init__(self, ibkr_stub: Any, schwab_broker: Any) -> None:
        self._stub = ibkr_stub
        self._schwab = schwab_broker

    async def refresh(self, instrument_id: int, db: AsyncSession) -> None:
        """Refresh sector for a single instrument. Failure does NOT raise."""
        try:
            asset_class = (
                await db.execute(
                    text("SELECT asset_class FROM instruments WHERE id = :id"),
                    {"id": instrument_id},
                )
            ).scalar_one_or_none()
            if asset_class is None:
                return

            if asset_class in _SYNTHETIC_CLASSES or asset_class not in _EQUITY_CLASSES:
                sector: str | None = f"_class:{asset_class.lower()}"
                sub_sector: str | None = None
            else:
                sector, sub_sector = await self._ibkr_sector(instrument_id, db)
                if sector is None:
                    return  # sidecar unavailable — preserve existing value

            await db.execute(
                text(
                    "UPDATE instruments SET sector = :sector, sub_sector = :sub_sector"
                    " WHERE id = :id"
                ),
                {"sector": sector, "sub_sector": sub_sector, "id": instrument_id},
            )
            await db.commit()
            m.orchestrator_sector_ingestion_total.labels(
                outcome="updated",
                source="ibkr" if asset_class in _EQUITY_CLASSES else "synthetic",
            ).inc()
        except Exception:
            log.exception("sector_ingestion_refresh_failed", instrument_id=instrument_id)
            m.orchestrator_sector_ingestion_total.labels(outcome="error", source="unknown").inc()

    async def _ibkr_sector(
        self, instrument_id: int, db: AsyncSession
    ) -> tuple[str | None, str | None]:
        """Return (sector, sub_sector) from IBKR, or (None, None) on failure."""
        conid = (
            await db.execute(
                text(
                    "SELECT conid FROM symbol_aliases"
                    " WHERE instrument_id = :id AND broker = 'ibkr'"
                    " LIMIT 1"
                ),
                {"id": instrument_id},
            )
        ).scalar_one_or_none()
        if conid is None:
            m.orchestrator_sector_ingestion_total.labels(outcome="skipped", source="ibkr").inc()
            return None, None
        try:
            resp = await self._stub.GetContractFundamentals(
                type("ContractRef", (), {"conid": str(conid)})()
            )
            industry = (resp.industry or "").strip().lower()
            category = (resp.category or "").strip().lower()
            if not industry:
                m.orchestrator_sector_ingestion_total.labels(outcome="skipped", source="ibkr").inc()
                return None, None
            return industry, category or None
        except Exception:
            log.warning("sector_ibkr_sidecar_failed", instrument_id=instrument_id)
            return None, None

    async def backfill_all(self, db: AsyncSession) -> dict:
        """Serial backfill of all instruments with sector IS NULL.

        Returns {processed, updated, skipped, errors} (errors capped at 100).
        """
        rows = (
            await db.execute(text("SELECT id FROM instruments WHERE sector IS NULL ORDER BY id"))
        ).all()
        instrument_ids = [r[0] for r in rows]

        processed = 0
        errors: list[dict] = []

        for iid in instrument_ids:
            processed += 1
            try:
                await self.refresh(iid, db)
            except Exception as exc:
                if len(errors) < 100:
                    errors.append({"instrument_id": iid, "reason": str(exc)})
            await asyncio.sleep(_IBKR_BATCH_DELAY_S)
            if processed % 200 == 0:
                log.info("sector_backfill_progress", processed=processed, total=len(instrument_ids))

        after = (
            await db.execute(text("SELECT COUNT(*) FROM instruments WHERE sector IS NOT NULL"))
        ).scalar_one()
        updated = int(after)
        skipped = processed - updated - len(errors)

        return {
            "processed": processed,
            "updated": max(updated, 0),
            "skipped": max(skipped, 0),
            "errors": errors,
        }
