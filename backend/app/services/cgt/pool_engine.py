from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
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


def _test_get_disposals(session: object) -> list[Disposal]:
    return list(_TEST_DISPOSALS)


def _uk_date(executed_at: datetime) -> date:
    return executed_at.astimezone(_LONDON).date()


def _tax_year(uk_date: date) -> int:
    if uk_date.month > 4 or (uk_date.month == 4 and uk_date.day >= 6):
        return uk_date.year
    return uk_date.year - 1


async def process(te: TaxEvent, session: AsyncSession) -> None:
    if te.is_short_open:
        await _handle_short_open(te, session)
    elif te.is_short_close:
        await _handle_short_close(te, session)
    elif te.side == "buy":
        await _handle_acquisition(te, session)
    else:
        await _handle_disposal(te, session)
    metrics.cgt_engine_processed_total.labels(cgt_track="pool", event_type=te.event_type).inc()


async def _handle_acquisition(te: TaxEvent, session: AsyncSession) -> None:
    cost = te.qty * te.price_gbp + te.commission_gbp
    async with session.begin_nested():
        await session.execute(
            text("""
                INSERT INTO s104_pool (account_id, instrument_id, qty,
                    total_cost_gbp, last_updated_at)
                VALUES (:a, :i, :q, :c, :t)
                ON CONFLICT (account_id, instrument_id) DO UPDATE SET
                    qty = s104_pool.qty + EXCLUDED.qty,
                    total_cost_gbp = s104_pool.total_cost_gbp + EXCLUDED.total_cost_gbp,
                    last_updated_at = EXCLUDED.last_updated_at
            """),
            {
                "a": te.account_id,
                "i": te.instrument_id,
                "q": te.qty,
                "c": cost,
                "t": te.executed_at,
            },
        )
    async with session.begin_nested():
        await session.execute(
            text("""
                INSERT INTO s104_pool_events
                    (account_id, instrument_id, tax_event_id, event_type,
                     qty_delta, cost_delta_gbp, pool_qty_after, pool_cost_after,
                     executed_at)
                VALUES (:a, :i, :te, 'acquisition', :q, :c, 0, 0, :t)
            """),
            {
                "a": te.account_id,
                "i": te.instrument_id,
                "te": te.id,
                "q": te.qty,
                "c": cost,
                "t": te.executed_at,
            },
        )
    async with session.begin_nested():
        await session.execute(
            text("UPDATE tax_events SET bb_remaining_qty = :q WHERE id = :id"),
            {"q": te.qty, "id": te.id},
        )


async def _handle_disposal(te: TaxEvent, session: AsyncSession) -> None:
    uk_date = _uk_date(te.executed_at)
    remaining = te.qty
    match_seq = 0

    # 1. Same-day buys
    sd_rows = await session.execute(
        text("""
            SELECT id, bb_remaining_qty, price_gbp, commission_gbp
            FROM tax_events
            WHERE account_id = :a AND cgt_class_key = :k AND side = 'buy'
              AND uk_trade_date = :d AND bb_remaining_qty > 0
              AND cgt_track = 'pool'
            ORDER BY executed_at
        """),
        {"a": te.account_id, "k": te.cgt_class_key, "d": uk_date},
    )
    for row in sd_rows.fetchall():
        if remaining <= 0:
            break
        matched = min(remaining, Decimal(str(row.bb_remaining_qty)))
        frac = matched / row.bb_remaining_qty
        cost = matched * Decimal(str(row.price_gbp)) + Decimal(str(row.commission_gbp)) * frac
        proceeds = matched * te.price_gbp - te.commission_gbp * (matched / te.qty)
        disposal = Disposal(
            disposal_tax_event_id=te.id,
            match_seq=match_seq,
            cgt_track="pool",
            tax_year=_tax_year(uk_date),
            disposal_date=uk_date,
            proceeds_gbp=proceeds,
            allowable_cost_gbp=cost,
            gain_gbp=proceeds - cost,
            match_type="same_day",
            account_id=te.account_id,
            instrument_id=te.instrument_id,
        )
        await _write_disposal(disposal, session)
        await _decrement_bb(row.id, matched, session)
        remaining -= matched
        match_seq += 1

    if remaining <= 0:
        return

    # 2. B&B — buys within next 30 calendar days
    bb_start = uk_date + timedelta(days=1)
    bb_end = uk_date + timedelta(days=30)
    bb_rows = await session.execute(
        text("""
            SELECT id, bb_remaining_qty, price_gbp, commission_gbp
            FROM tax_events
            WHERE account_id = :a AND cgt_class_key = :k AND side = 'buy'
              AND uk_trade_date BETWEEN :d1 AND :d2 AND bb_remaining_qty > 0
              AND cgt_track = 'pool'
            ORDER BY uk_trade_date, executed_at
        """),
        {"a": te.account_id, "k": te.cgt_class_key, "d1": bb_start, "d2": bb_end},
    )
    for row in bb_rows.fetchall():
        if remaining <= 0:
            break
        matched = min(remaining, Decimal(str(row.bb_remaining_qty)))
        frac = matched / row.bb_remaining_qty
        cost = matched * Decimal(str(row.price_gbp)) + Decimal(str(row.commission_gbp)) * frac
        proceeds = matched * te.price_gbp - te.commission_gbp * (matched / te.qty)
        disposal = Disposal(
            disposal_tax_event_id=te.id,
            match_seq=match_seq,
            cgt_track="pool",
            tax_year=_tax_year(uk_date),
            disposal_date=uk_date,
            proceeds_gbp=proceeds,
            allowable_cost_gbp=cost,
            gain_gbp=proceeds - cost,
            match_type="bb_30",
            account_id=te.account_id,
            instrument_id=te.instrument_id,
        )
        await _write_disposal(disposal, session)
        await _decrement_bb(row.id, matched, session)
        remaining -= matched
        match_seq += 1

    if remaining <= 0:
        return

    # 3. S104 remainder
    pool_row = await session.execute(
        text("""
            SELECT qty, total_cost_gbp, pool_avg_cost_gbp FROM s104_pool
            WHERE account_id = :a AND instrument_id = :i
        """),
        {"a": te.account_id, "i": te.instrument_id},
    )
    pool = pool_row.fetchone()
    avg_cost = Decimal(str(pool.pool_avg_cost_gbp)) if pool else Decimal("0")
    cost = remaining * avg_cost + te.commission_gbp * (remaining / te.qty)
    proceeds = remaining * te.price_gbp - te.commission_gbp * (remaining / te.qty)
    disposal = Disposal(
        disposal_tax_event_id=te.id,
        match_seq=match_seq,
        cgt_track="pool",
        tax_year=_tax_year(uk_date),
        disposal_date=uk_date,
        proceeds_gbp=proceeds,
        allowable_cost_gbp=cost,
        gain_gbp=proceeds - cost,
        match_type="s104",
        account_id=te.account_id,
        instrument_id=te.instrument_id,
    )
    await _write_disposal(disposal, session)
    async with session.begin_nested():
        await session.execute(
            text("""
                UPDATE s104_pool
                SET qty = qty - :q,
                    total_cost_gbp = GREATEST(0, total_cost_gbp - :c),
                    last_updated_at = :t
                WHERE account_id = :a AND instrument_id = :i
            """),
            {
                "q": remaining,
                "c": avg_cost * remaining,
                "a": te.account_id,
                "i": te.instrument_id,
                "t": te.executed_at,
            },
        )
    metrics.cgt_disposal_inserted_total.labels(match_type="s104").inc()


async def _handle_short_open(te: TaxEvent, session: AsyncSession) -> None:
    proceeds = te.qty * te.price_gbp - te.commission_gbp
    async with session.begin_nested():
        await session.execute(
            text("""
                INSERT INTO short_obligations
                    (account_id, instrument_id, open_tax_event_id,
                     open_qty, open_proceeds_gbp, status, opened_at)
                VALUES (:a, :i, :te, :q, :p, 'open', :t)
            """),
            {
                "a": te.account_id,
                "i": te.instrument_id,
                "te": te.id,
                "q": te.qty,
                "p": proceeds,
                "t": te.executed_at,
            },
        )
    metrics.cgt_short_obligation_open_count.inc()


async def _handle_short_close(te: TaxEvent, session: AsyncSession) -> None:
    rows = await session.execute(
        text("""
            SELECT id, open_qty, open_proceeds_gbp FROM short_obligations
            WHERE account_id = :a AND instrument_id = :i AND status = 'open'
            ORDER BY opened_at LIMIT 1
        """),
        {"a": te.account_id, "i": te.instrument_id},
    )
    obligation = rows.fetchone()
    if obligation is None:
        log.warning(
            "cgt.pool.short_close_no_obligation",
            instrument_id=te.instrument_id,
            account_id=str(te.account_id),
        )
        return
    close_cost = te.qty * te.price_gbp + te.commission_gbp
    gain = Decimal(str(obligation.open_proceeds_gbp)) - close_cost
    uk_date = _uk_date(te.executed_at)
    async with session.begin_nested():
        await session.execute(
            text("""
                UPDATE short_obligations
                SET status = 'closed', close_tax_event_id = :cte,
                    close_qty = :q, close_cost_gbp = :cc,
                    gain_gbp = :g, closed_at = :t
                WHERE id = :id
            """),
            {
                "cte": te.id,
                "q": te.qty,
                "cc": close_cost,
                "g": gain,
                "t": te.executed_at,
                "id": obligation.id,
            },
        )
    disposal = Disposal(
        disposal_tax_event_id=te.id,
        match_seq=0,
        cgt_track="pool",
        tax_year=_tax_year(uk_date),
        disposal_date=uk_date,
        proceeds_gbp=Decimal(str(obligation.open_proceeds_gbp)),
        allowable_cost_gbp=close_cost,
        gain_gbp=gain,
        match_type="short",
        account_id=te.account_id,
        instrument_id=te.instrument_id,
        short_obligation_id=obligation.id,
    )
    await _write_disposal(disposal, session)
    metrics.cgt_disposal_inserted_total.labels(match_type="short").inc()
    metrics.cgt_short_closed_total.inc()


async def _write_disposal(disposal: Disposal, session: AsyncSession) -> None:
    _TEST_DISPOSALS.append(disposal)
    async with session.begin_nested():
        await session.execute(
            text("""
                INSERT INTO cgt_disposals
                    (account_id, instrument_id, disposal_tax_event_id, match_seq,
                     cgt_track, tax_year, disposal_date, proceeds_gbp,
                     allowable_cost_gbp, gain_gbp, match_type,
                     pool_event_id, short_obligation_id, derivative_id)
                VALUES (:a, :i, :dte, :ms, :ct, :ty, :dd, :p, :c, :g, :mt,
                        :pe, :so, :dp)
                ON CONFLICT (disposal_tax_event_id, match_seq) DO NOTHING
            """),
            {
                "a": disposal.account_id,
                "i": disposal.instrument_id,
                "dte": disposal.disposal_tax_event_id,
                "ms": disposal.match_seq,
                "ct": disposal.cgt_track,
                "ty": disposal.tax_year,
                "dd": disposal.disposal_date,
                "p": disposal.proceeds_gbp,
                "c": disposal.allowable_cost_gbp,
                "g": disposal.gain_gbp,
                "mt": disposal.match_type,
                "pe": disposal.pool_event_id,
                "so": disposal.short_obligation_id,
                "dp": disposal.derivative_id,
            },
        )
    metrics.cgt_disposal_inserted_total.labels(match_type=disposal.match_type).inc()


async def _decrement_bb(event_id: uuid.UUID, qty: Decimal, session: AsyncSession) -> None:
    await session.execute(
        text("UPDATE tax_events SET bb_remaining_qty = bb_remaining_qty - :q WHERE id = :id"),
        {"q": qty, "id": event_id},
    )
