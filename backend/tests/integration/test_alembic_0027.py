"""Phase 9 Task 6 — verify bar_backfill_jobs partial-unique index + status CHECK."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration]


@pytest.mark.asyncio
async def test_0027_partial_unique_blocks_concurrent_pending(
    db_session: AsyncSession, seed_instrument_aapl
) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    await db_session.execute(
        text(
            """
            INSERT INTO bar_backfill_jobs
              (instrument_id, source, timeframe, range_start, range_end, status)
            VALUES
              (:inst, 'schwab', '1m', '2026-04-01', '2026-04-30', 'pending')
            """
        ),
        {"inst": inst_id},
    )
    await db_session.flush()
    with pytest.raises(IntegrityError, match="bbj_unique_pending_idx"):
        await db_session.execute(
            text(
                """
                INSERT INTO bar_backfill_jobs
                  (instrument_id, source, timeframe, range_start, range_end, status)
                VALUES
                  (:inst, 'schwab', '1m', '2026-04-01', '2026-04-30', 'in_progress')
                """
            ),
            {"inst": inst_id},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_0027_partial_unique_allows_done_then_new(
    db_session: AsyncSession, seed_instrument_aapl
) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    await db_session.execute(
        text(
            """
            INSERT INTO bar_backfill_jobs
              (instrument_id, source, timeframe, range_start, range_end, status)
            VALUES
              (:inst, 'schwab', '1m', '2026-04-01', '2026-04-30', 'done')
            """
        ),
        {"inst": inst_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO bar_backfill_jobs
              (instrument_id, source, timeframe, range_start, range_end, status)
            VALUES
              (:inst, 'schwab', '1m', '2026-04-01', '2026-04-30', 'pending')
            """
        ),
        {"inst": inst_id},
    )
    await db_session.flush()
    cnt = (
        await db_session.execute(
            text("SELECT COUNT(*)::int AS c FROM bar_backfill_jobs WHERE instrument_id = :inst"),
            {"inst": inst_id},
        )
    ).scalar_one()
    assert cnt == 2


@pytest.mark.asyncio
async def test_0027_status_check(db_session: AsyncSession, seed_instrument_aapl) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    with pytest.raises(IntegrityError, match="bbj_status_chk"):
        await db_session.execute(
            text(
                """
                INSERT INTO bar_backfill_jobs
                  (instrument_id, source, timeframe, range_start, range_end, status)
                VALUES
                  (:inst, 'schwab', '1m', '2026-04-01', '2026-04-30', 'unknown')
                """
            ),
            {"inst": inst_id},
        )
        await db_session.flush()
