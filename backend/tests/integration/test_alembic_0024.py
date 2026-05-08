"""Phase 9 Task 4 — verify bars_1s + bars_1m hypertables, CHECK constraints, retention."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration]


@pytest.mark.asyncio
async def test_0024_hypertables_created(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_name IN ('bars_1s','bars_1m')"
            )
        )
    ).all()
    names = {r.hypertable_name for r in rows}
    assert names == {"bars_1s", "bars_1m"}, f"missing hypertables: {names}"


@pytest.mark.asyncio
async def test_0024_pk_is_inst_bucket(db_session: AsyncSession) -> None:
    for tbl in ("bars_1s", "bars_1m"):
        row = (
            await db_session.execute(
                text(
                    """
                    SELECT array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum)) AS cols
                      FROM pg_constraint c
                      JOIN pg_attribute a
                        ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                     WHERE c.conrelid = CAST(:tbl AS regclass) AND c.contype = 'p'
                     GROUP BY c.conname
                    """
                ),
                {"tbl": tbl},
            )
        ).first()
        assert row is not None, f"PK missing on {tbl}"
        assert list(row.cols) == ["instrument_id", "bucket_start"], (
            f"{tbl} PK is {row.cols}, expected [instrument_id, bucket_start]"
        )


@pytest.mark.asyncio
async def test_0024_volume_source_check(db_session: AsyncSession, seed_instrument_aapl) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    with pytest.raises(IntegrityError, match="bars_1s_volume_source_chk"):
        await db_session.execute(
            text(
                """
                INSERT INTO bars_1s
                  (instrument_id, bucket_start, source, source_priority,
                   open, high, low, close, volume, volume_source, trade_count)
                VALUES
                  (:inst, NOW(), 'aggregator-schwab', 99,
                   100, 100, 100, 100, 1, 'invalid_label', 1)
                """
            ),
            {"inst": inst_id},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_0024_priority_check_on_1m(db_session: AsyncSession, seed_instrument_aapl) -> None:
    inst_id = await seed_instrument_aapl(db_session)
    with pytest.raises(IntegrityError, match="bars_1m_priority_chk"):
        await db_session.execute(
            text(
                """
                INSERT INTO bars_1m
                  (instrument_id, bucket_start, source, source_priority,
                   open, high, low, close, volume, volume_source, trade_count)
                VALUES
                  (:inst, NOW(), 'foo', 7,
                   100, 100, 100, 100, 1, 'tape', 1)
                """
            ),
            {"inst": inst_id},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_0024_retention_policies(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                """
                SELECT hypertable_name, config->>'drop_after' AS drop_after
                  FROM timescaledb_information.jobs
                 WHERE proc_name = 'policy_retention'
                   AND hypertable_name IN ('bars_1s','bars_1m')
                """
            )
        )
    ).all()
    by_table = {r.hypertable_name: r.drop_after for r in rows}
    assert by_table.get("bars_1s") == "7 days", (
        f"bars_1s retention is {by_table.get('bars_1s')}, expected '7 days'"
    )
    assert by_table.get("bars_1m") in ("6 mons", "180 days"), (
        f"bars_1m retention is {by_table.get('bars_1m')}, expected '6 mons' or '180 days'"
    )
