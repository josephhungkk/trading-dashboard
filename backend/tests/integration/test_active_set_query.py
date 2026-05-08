"""Phase 9 Task 9 — verify BarService.active_set query semantics + 1000-row cap."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bar_service import BarService
from scripts.seed_phase9_app_config import seed_phase9_app_config

pytestmark = [pytest.mark.integration]


@pytest.mark.asyncio
async def test_active_set_unions_positions_watchlist_chart_layouts(
    db_session: AsyncSession, seed_instrument_aapl
) -> None:
    await seed_phase9_app_config(db_session)
    inst_id = await seed_instrument_aapl(db_session)
    await db_session.execute(
        text(
            """
            INSERT INTO positions
              (broker_id, account_id, instrument_id, symbol, qty)
            VALUES
              ('schwab', '00000000-0000-0000-0000-000000000001', :inst, 'AAPL', 1)
            """
        ),
        {"inst": inst_id},
    )
    await db_session.flush()

    svc = BarService()
    rows = await svc.active_set(db_session)
    instrument_ids = {r.instrument_id for r in rows}
    assert inst_id in instrument_ids


@pytest.mark.asyncio
async def test_active_set_caps_at_1000(
    db_session: AsyncSession, bulk_seed_1500_instruments
) -> None:
    await seed_phase9_app_config(db_session)
    await bulk_seed_1500_instruments(db_session)

    svc = BarService()
    rows = await svc.active_set(db_session)
    assert len(rows) == 1000


@pytest.mark.asyncio
async def test_active_set_returns_named_tuples_sorted_desc(
    db_session: AsyncSession, seed_instrument_aapl
) -> None:
    await seed_phase9_app_config(db_session)
    inst_id = await seed_instrument_aapl(db_session)
    await db_session.execute(
        text(
            """
            INSERT INTO watchlist_entries
              (broker_id, symbol, exchange, instrument_id)
            VALUES
              ('schwab', 'AAPL', 'NYSE', :inst)
            """
        ),
        {"inst": inst_id},
    )
    await db_session.flush()

    svc = BarService()
    rows = await svc.active_set(db_session)
    assert all(hasattr(r, "instrument_id") and hasattr(r, "recency_score") for r in rows)
    scores = [r.recency_score for r in rows]
    assert scores == sorted(scores, reverse=True)
