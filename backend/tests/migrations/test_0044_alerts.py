"""Phase 11b-A1 migration assertions: alerts + alert_fires + alert_fire_context
+ bars_1m NOTIFY trigger + GIN indexes."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_alerts_table_exists(session: AsyncSession) -> None:
    res = await session.execute(text("SELECT to_regclass('public.alerts')"))
    assert res.scalar() is not None


@pytest.mark.asyncio
async def test_alert_fires_is_hypertable(session: AsyncSession) -> None:
    res = await session.execute(
        text(
            "SELECT count(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'alert_fires'"
        )
    )
    assert res.scalar() == 1


@pytest.mark.asyncio
async def test_alert_fire_context_table_exists(session: AsyncSession) -> None:
    res = await session.execute(text("SELECT to_regclass('public.alert_fire_context')"))
    assert res.scalar() is not None


@pytest.mark.asyncio
async def test_predicate_gin_index_exists(session: AsyncSession) -> None:
    res = await session.execute(
        text("SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_alerts_predicate_gin'")
    )
    assert res.scalar() == 1


@pytest.mark.asyncio
async def test_requires_capabilities_gin_index_exists(session: AsyncSession) -> None:
    res = await session.execute(
        text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE indexname = 'idx_alerts_requires_capabilities_gin'"
        )
    )
    assert res.scalar() == 1


@pytest.mark.asyncio
async def test_active_by_subject_partial_index_exists(session: AsyncSession) -> None:
    res = await session.execute(
        text("SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_alerts_active_by_subject'")
    )
    assert res.scalar() == 1


@pytest.mark.asyncio
async def test_bars_1m_notify_trigger_exists(session: AsyncSession) -> None:
    res = await session.execute(
        text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_bars_1m_notify'")
    )
    assert res.scalar() == 1


@pytest.mark.asyncio
async def test_notify_function_exists(session: AsyncSession) -> None:
    res = await session.execute(
        text("SELECT count(*) FROM pg_proc WHERE proname = 'notify_bars_1m_insert'")
    )
    assert res.scalar() == 1
