"""Reconcile live fills vs broker-statement fills."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


async def reconcile(account_id: uuid.UUID, session: AsyncSession) -> dict[str, int]:
    """Match broker-statement tax_events against live fills (±60s window).

    Orphaned live fills (no statement match) are logged for manual review.
    """
    orphaned = await session.execute(
        text("""
        SELECT te.id, te.external_event_id, te.executed_at
        FROM tax_events te
        WHERE te.account_id = :a
          AND te.source = 'fill_live'
          AND NOT EXISTS (
            SELECT 1 FROM tax_events bs
            WHERE bs.account_id = :a
              AND bs.source = 'broker_statement'
              AND bs.instrument_id = te.instrument_id
              AND bs.side = te.side
              AND bs.qty = te.qty
              AND ABS(EXTRACT(EPOCH FROM (bs.executed_at - te.executed_at))) <= 60
          )
    """),
        {"a": account_id},
    )

    orphan_ids = [row.id for row in orphaned.fetchall()]
    if orphan_ids:
        log.warning(
            "cgt.reconciler.orphaned_live_fills",
            account_id=str(account_id),
            count=len(orphan_ids),
        )

    return {"orphaned_live_fills": len(orphan_ids)}
