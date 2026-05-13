"""Phase 11b chunk B5: nightly cleanup of ``alert_fire_context`` rows older than 90d.

Run via apscheduler in app lifespan (chunk-B-close wiring task). The
hypertable ``alert_fires`` has its own TimescaleDB retention policy (1y,
add_retention_policy in alembic 0044). Only the non-hypertable
``alert_fire_context`` table needs an explicit sweep — its rows can
contain PII (NLV, positions) so the 90d window is shorter than the
fire-history retention.

The sweep is small in practice (about 10 fires/day x few users x 90d, a
few thousand rows) so we issue one DELETE ... RETURNING id and report the count.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


async def sweep_alert_fire_context(db: AsyncSession, *, retention_days: int = 90) -> int:
    """Delete ``alert_fire_context`` rows older than ``retention_days``.

    Returns the count deleted via ``cursor.rowcount`` — does NOT materialize
    deleted rows in Python, so a widened retention call can't blow up memory
    on a backlog sweep. Caller (apscheduler job) logs the count.
    """
    result: CursorResult[object] = await db.execute(  # type: ignore[assignment]
        text("DELETE FROM alert_fire_context WHERE created_at < now() - make_interval(days => :d)"),
        {"d": retention_days},
    )
    count = result.rowcount or 0
    await db.commit()
    return count
