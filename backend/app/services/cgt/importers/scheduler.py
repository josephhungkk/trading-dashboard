"""APScheduler job wiring for CGT importers."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.cgt import metrics
from app.services.cgt.importers import ibkr_flex
from app.services.cgt.importers.reconciler import reconcile

log = structlog.get_logger(__name__)


async def run_ibkr_flex_job(
    account_id: uuid.UUID,
    flex_token: str,
    flex_query_id: str,
    db_factory: async_sessionmaker,
) -> None:
    """Nightly IBKR Flex pull — called by APScheduler."""
    start = time.monotonic()
    try:
        async with db_factory() as session:
            result = await ibkr_flex.run_import(
                account_id=account_id,
                flex_token=flex_token,
                flex_query_id=flex_query_id,
                session=session,
            )
            await reconcile(account_id, session)
            await session.commit()
        log.info("cgt.scheduler.ibkr_flex_done", **result)
    except Exception as exc:
        log.exception("cgt.scheduler.ibkr_flex_failed", exc=str(exc))
    finally:
        elapsed = time.monotonic() - start
        metrics.cgt_importer_duration_seconds.labels(broker="ibkr").observe(elapsed)
