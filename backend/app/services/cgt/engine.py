from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cgt import corporate, derivative_engine, metrics, pool_engine
from app.services.cgt.types import TaxEvent

log = structlog.get_logger(__name__)


async def process(te: TaxEvent, session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"cgt:{te.account_id}:{te.instrument_id}"},
    )
    with metrics.cgt_engine_process_seconds.labels(cgt_track=te.cgt_track).time():
        try:
            if te.cgt_track == "exempt":
                pass
            elif te.cgt_track == "pool":
                if te.event_type.startswith("corp_action"):
                    await corporate.process(te, session)
                else:
                    await pool_engine.process(te, session)
            elif te.cgt_track == "derivative":
                await derivative_engine.process(te, session)
            else:
                log.error("cgt.engine.unknown_track", cgt_track=te.cgt_track)
                return
            metrics.cgt_engine_processed_total.labels(
                cgt_track=te.cgt_track, event_type=te.event_type
            ).inc()
        except Exception as exc:
            metrics.cgt_engine_failed_total.labels(reason=type(exc).__name__).inc()
            log.exception(
                "cgt.engine.process_failed",
                exc=str(exc),
                instrument_id=te.instrument_id,
                account_id=str(te.account_id),
            )
            raise


async def recompute(account_id: uuid.UUID, instrument_id: int, session: AsyncSession) -> None:
    with metrics.cgt_recompute_seconds.time():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"cgt:{account_id}:{instrument_id}"},
        )
        await session.execute(
            text("DELETE FROM cgt_disposals WHERE account_id = :a AND instrument_id = :i"),
            {"a": account_id, "i": instrument_id},
        )
        await session.execute(
            text("DELETE FROM s104_pool_events WHERE account_id = :a AND instrument_id = :i"),
            {"a": account_id, "i": instrument_id},
        )
        await session.execute(
            text("""
                DELETE FROM short_obligations
                WHERE account_id = :a AND instrument_id = :i AND status = 'closed'
            """),
            {"a": account_id, "i": instrument_id},
        )
        await session.execute(
            text("""
                INSERT INTO s104_pool (account_id, instrument_id, qty, total_cost_gbp,
                    last_updated_at)
                VALUES (:a, :i, 0, 0, now())
                ON CONFLICT (account_id, instrument_id) DO UPDATE
                    SET qty = 0, total_cost_gbp = 0
            """),
            {"a": account_id, "i": instrument_id},
        )
        events_result = await session.execute(
            text("""
                SELECT id, account_id, instrument_id, cgt_track, event_type, side,
                       is_short_open, is_short_close, qty, price_gbp,
                       commission_native, commission_currency, commission_gbp,
                       fx_rate, fx_source, original_currency, cgt_class_key,
                       bb_remaining_qty, executed_at, bot_id, transfer_group_id,
                       notes, fill_id, leg_index, broker_statement_id,
                       external_event_id, source
                FROM tax_events
                WHERE account_id = :a AND instrument_id = :i
                ORDER BY executed_at
            """),
            {"a": account_id, "i": instrument_id},
        )
        for row in events_result.fetchall():
            te = _row_to_tax_event(row)
            try:
                async with session.begin_nested():
                    await process(te, session)
            except Exception as exc:
                log.error("cgt.engine.recompute_event_failed", event_id=str(te.id), exc=str(exc))

        log.info(
            "cgt.engine.recompute_complete", account_id=str(account_id), instrument_id=instrument_id
        )
        metrics.cgt_recompute_triggered_total.labels(trigger="recompute").inc()


def _row_to_tax_event(row: Any) -> TaxEvent:
    return TaxEvent(
        id=row.id,
        account_id=row.account_id,
        instrument_id=row.instrument_id,
        cgt_track=row.cgt_track,
        event_type=row.event_type,
        side=row.side,
        is_short_open=row.is_short_open,
        is_short_close=row.is_short_close,
        qty=Decimal(str(row.qty)),
        price_gbp=Decimal(str(row.price_gbp)),
        commission_native=Decimal(str(row.commission_native)),
        commission_currency=row.commission_currency,
        commission_gbp=Decimal(str(row.commission_gbp)),
        fx_rate=Decimal(str(row.fx_rate)),
        fx_source=row.fx_source,
        original_currency=row.original_currency,
        cgt_class_key=row.cgt_class_key,
        bb_remaining_qty=Decimal(str(row.bb_remaining_qty)),
        executed_at=row.executed_at,
        fill_id=row.fill_id,
        leg_index=row.leg_index or 0,
        broker_statement_id=row.broker_statement_id,
        external_event_id=row.external_event_id,
        source=row.source,
        bot_id=row.bot_id,
        notes=row.notes,
    )
