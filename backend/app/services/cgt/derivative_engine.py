from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cgt import metrics
from app.services.cgt.types import Disposal, TaxEvent

log = structlog.get_logger(__name__)
_LONDON = ZoneInfo("Europe/London")

_TEST_DISPOSALS: list[Disposal] = []
_TEST_POSITIONS: list[dict] = []


def _test_get_disposals() -> list[Disposal]:
    return list(_TEST_DISPOSALS)


def _uk_date(executed_at: datetime) -> date:
    return executed_at.astimezone(_LONDON).date()


def _tax_year(uk_date: date) -> int:
    if uk_date.month > 4 or (uk_date.month == 4 and uk_date.day >= 6):
        return uk_date.year
    return uk_date.year - 1


async def process(te: TaxEvent, session: AsyncSession) -> None:
    pos_side = "long" if te.side == "buy" else "short"

    result = await session.execute(
        text("""
            SELECT id, qty, total_proceeds_gbp, total_cost_gbp, side
            FROM derivative_positions
            WHERE account_id = :a AND instrument_id = :i AND status = 'open'
            ORDER BY opened_at
            LIMIT 1
        """),
        {"a": te.account_id, "i": te.instrument_id},
    )
    open_pos = result.fetchone()

    if open_pos is None:
        is_long = te.side == "buy"
        proc_gbp = te.qty * te.price_gbp if not is_long else Decimal("0")
        cost_gbp = te.qty * te.price_gbp if is_long else Decimal("0")
        pos_id = uuid.uuid4()
        async with session.begin_nested():
            await session.execute(
                text("""
                    INSERT INTO derivative_positions
                        (id, account_id, instrument_id, open_tax_event_id, side, qty,
                         total_proceeds_gbp, total_cost_gbp, status, opened_at)
                    VALUES (:id, :a, :i, :te, :s, :q, :p, :c, 'open', :t)
                """),
                {
                    "id": pos_id,
                    "a": te.account_id,
                    "i": te.instrument_id,
                    "te": te.id,
                    "s": pos_side,
                    "q": te.qty,
                    "p": proc_gbp,
                    "c": cost_gbp,
                    "t": te.executed_at,
                },
            )
        _TEST_POSITIONS.append({"id": pos_id, "side": pos_side, "qty": te.qty})
        metrics.cgt_engine_processed_total.labels(
            cgt_track="derivative", event_type=te.event_type
        ).inc()
        return

    pos_proceeds = Decimal(str(open_pos.total_proceeds_gbp))
    pos_cost = Decimal(str(open_pos.total_cost_gbp))
    is_close_long = open_pos.side == "long"
    close_amount = te.qty * te.price_gbp - te.commission_gbp

    if is_close_long:
        pos_proceeds += close_amount
    else:
        pos_cost += close_amount

    gain = pos_proceeds - pos_cost
    uk_date = _uk_date(te.executed_at)

    disposal = Disposal(
        disposal_tax_event_id=te.id,
        match_seq=0,
        cgt_track="derivative",
        tax_year=_tax_year(uk_date),
        disposal_date=uk_date,
        proceeds_gbp=pos_proceeds,
        allowable_cost_gbp=pos_cost,
        gain_gbp=gain,
        match_type="derivative",
        account_id=te.account_id,
        instrument_id=te.instrument_id,
        derivative_id=open_pos.id,
    )
    async with session.begin_nested():
        await session.execute(
            text("""
                UPDATE derivative_positions
                SET status = 'closed', close_tax_event_id = :cte,
                    total_proceeds_gbp = :p, total_cost_gbp = :c,
                    gain_gbp = :g, closed_at = :t
                WHERE id = :id
            """),
            {
                "cte": te.id,
                "p": pos_proceeds,
                "c": pos_cost,
                "g": gain,
                "t": te.executed_at,
                "id": open_pos.id,
            },
        )
        await session.execute(
            text("""
                INSERT INTO cgt_disposals
                    (account_id, instrument_id, disposal_tax_event_id, match_seq,
                     cgt_track, tax_year, disposal_date, proceeds_gbp,
                     allowable_cost_gbp, gain_gbp, match_type, derivative_id)
                VALUES (:a, :i, :dte, 0, 'derivative', :ty, :dd, :p, :c, :g,
                        'derivative', :did)
                ON CONFLICT (disposal_tax_event_id, match_seq) DO NOTHING
            """),
            {
                "a": te.account_id,
                "i": te.instrument_id,
                "dte": te.id,
                "ty": _tax_year(uk_date),
                "dd": uk_date,
                "p": pos_proceeds,
                "c": pos_cost,
                "g": gain,
                "did": open_pos.id,
            },
        )
    _TEST_DISPOSALS.append(disposal)
    metrics.cgt_disposal_inserted_total.labels(match_type="derivative").inc()
    metrics.cgt_engine_processed_total.labels(
        cgt_track="derivative", event_type=te.event_type
    ).inc()
    log.info("cgt.derivative.closed", gain=str(gain), instrument_id=te.instrument_id)
