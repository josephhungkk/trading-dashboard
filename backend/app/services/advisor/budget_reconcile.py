from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.advisor.metrics import advisor_budget_reconcile_delta_usd

logger = structlog.get_logger(__name__)


async def reconcile_budget_for_bot(bot_id: UUID, redis, db: AsyncSession) -> None:
    today_start = datetime.combine(date.today(), time.min, tzinfo=UTC)
    caller = f"advisor:bot:{bot_id}"
    result = await db.execute(
        text(
            "SELECT COALESCE(SUM(cost_usd), 0) "
            "FROM ai_completions WHERE caller = :caller AND ts >= :today_start"
        ),
        {"caller": caller, "today_start": today_start},
    )
    actual_usd = Decimal(str(result.scalar_one() or 0))
    actual_cents = int((actual_usd * Decimal("100")).to_integral_value())

    key = f"advisor:spend_estimate_cents:{bot_id}:{date.today().isoformat()}"
    optimistic_raw = await redis.get(key)
    optimistic_cents = int(optimistic_raw or 0)
    delta_usd = Decimal(actual_cents - optimistic_cents) / Decimal("100")

    await redis.set(key, actual_cents, ex=172800)
    advisor_budget_reconcile_delta_usd.set(float(delta_usd))


async def run_budget_reconcile_loop(advisor_service, db_factory, redis) -> None:
    while True:
        try:
            async with db_factory() as db:
                result = await db.execute(
                    text(
                        "SELECT id FROM bots "
                        "WHERE deleted_at IS NULL "
                        "AND status IN ('starting', 'running', 'pausing')"
                    )
                )
                bot_ids = [row[0] for row in result]
                for bot_id in bot_ids:
                    try:
                        await reconcile_budget_for_bot(bot_id, redis, db)
                    except Exception as exc:
                        logger.warning(
                            "advisor_budget_reconcile_bot_failed", bot_id=str(bot_id), exc_info=exc
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("advisor_budget_reconcile_loop_failed", exc_info=exc)
        await asyncio.sleep(300)
