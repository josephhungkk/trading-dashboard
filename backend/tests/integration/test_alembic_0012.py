"""Phase 8b migration 0012: broker_features seed."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_broker_features_seeded_14_rows(db_session: AsyncSession) -> None:
    n = (await db_session.execute(text("SELECT COUNT(*) FROM broker_features"))).scalar_one()
    assert n == 14


@pytest.mark.asyncio
async def test_modify_supported_per_broker(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT broker_id, is_supported FROM broker_features "
                "WHERE feature='modify' ORDER BY broker_id"
            )
        )
    ).all()
    assert {r.broker_id: r.is_supported for r in rows} == {
        "futu": False,
        "ibkr": True,
        "schwab": True,
    }


@pytest.mark.asyncio
async def test_gtd_max_days_int_values(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT broker_id, int_value FROM broker_features "
                "WHERE feature='gtd_max_days' ORDER BY broker_id"
            )
        )
    ).all()
    assert {r.broker_id: r.int_value for r in rows} == {
        "futu": 30,
        "ibkr": 90,
        "schwab": 60,
    }


@pytest.mark.asyncio
async def test_session_cutoff_minutes_for_exchange_codes(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT broker_id, int_value FROM broker_features "
                "WHERE feature='session_cutoff_minutes' ORDER BY broker_id"
            )
        )
    ).all()
    assert {r.broker_id: r.int_value for r in rows} == {
        "hkex": 0,
        "nyse": 10,
    }


@pytest.mark.asyncio
async def test_notes_printable_ascii_only(db_session: AsyncSession) -> None:
    n = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM broker_features WHERE notes !~ '^[\\x20-\\x7E]*$'")
        )
    ).scalar_one()
    assert n == 0
