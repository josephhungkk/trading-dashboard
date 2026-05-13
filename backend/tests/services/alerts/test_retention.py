"""Phase 11b chunk B5: alert_fire_context retention sweep tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alerts.retention import sweep_alert_fire_context


async def _clear_table(session: AsyncSession) -> None:
    await session.execute(text("DELETE FROM alert_fire_context"))
    await session.commit()


async def test_sweep_deletes_rows_older_than_90d(session: AsyncSession) -> None:
    await _clear_table(session)
    old = datetime.now(UTC) - timedelta(days=100)
    fresh = datetime.now(UTC) - timedelta(days=30)
    await session.execute(
        text(
            "INSERT INTO alert_fire_context "
            "(alert_id, fired_at, evaluated_values, created_at) "
            "VALUES (1, :t1, '{}'::jsonb, :t1), "
            "       (1, :t2, '{}'::jsonb, :t2)"
        ),
        {"t1": old, "t2": fresh},
    )
    await session.commit()

    deleted = await sweep_alert_fire_context(session)
    assert deleted == 1

    remaining = (
        await session.execute(text("SELECT count(*) FROM alert_fire_context"))
    ).scalar_one()
    assert remaining == 1


async def test_sweep_empty_table_returns_zero(session: AsyncSession) -> None:
    await _clear_table(session)
    deleted = await sweep_alert_fire_context(session)
    assert deleted == 0


async def test_sweep_custom_retention(session: AsyncSession) -> None:
    """retention_days kwarg lets callers override the default 90d."""
    await _clear_table(session)
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)
    await session.execute(
        text(
            "INSERT INTO alert_fire_context "
            "(alert_id, fired_at, evaluated_values, created_at) "
            "VALUES (1, :t, '{}'::jsonb, :t)"
        ),
        {"t": seven_days_ago},
    )
    await session.commit()

    # 90d retention → row stays.
    deleted_90 = await sweep_alert_fire_context(session, retention_days=90)
    assert deleted_90 == 0

    # 1d retention → row gone.
    deleted_1 = await sweep_alert_fire_context(session, retention_days=1)
    assert deleted_1 == 1
