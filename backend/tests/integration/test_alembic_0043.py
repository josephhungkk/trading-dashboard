"""Phase 11a-CI-debt-2: verify 0043 widens risk_decisions.attempt_kind CHECK.

Migration 0036 created the CHECK allowing only
``('place_order', 'modify_order')``. Phase 10a.5.1 C1 then added a preview
audit path writing ``attempt_kind='preview'`` — every write silently
``CheckViolation``'d and was swallowed by the audit try/except. 0043 widens
the CHECK to include ``'preview'``.

This test verifies the post-upgrade state allows ``'preview'`` and the
downgrade hook narrows the constraint back and purges any preview rows.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.config import settings


def _alembic_config() -> Config:
    """Mirrors test_alembic_0019._alembic_config — async-driver URL,
    fileConfig cleared so caplog handlers survive the run.
    """
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


@pytest.mark.asyncio
async def test_0043_check_allows_preview_after_upgrade(
    db_session: AsyncSession,
) -> None:
    """Post-upgrade (current head) state: CHECK allows 'preview'."""
    constraint_def = (
        await db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE conname = 'risk_decisions_attempt_kind_check'"
            )
        )
    ).scalar_one()
    assert "'preview'" in constraint_def
    assert "'place_order'" in constraint_def
    assert "'modify_order'" in constraint_def


@pytest.mark.asyncio
async def test_0043_preview_insert_succeeds(session: AsyncSession) -> None:
    """A risk_decisions row with attempt_kind='preview' inserts cleanly.

    Pre-0043 this raised CheckViolation. The audit path swallowed it but
    the actual constraint must allow the row now.
    """
    s = session
    aid = uuid4()
    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, gateway_label, account_number, alias, mode, "
            "currency_base, display_order, first_seen_at, last_seen_at, "
            "last_seen_via) "
            "VALUES (:id, 'ibkr', 'phase11a-test', :acct, 'phase11a-test', "
            "'paper', 'USD', 0, now(), now(), 'phase11a-test')"
        ),
        {"id": aid, "acct": f"U_0043_{uuid4().hex[:8]}"},
    )
    await s.execute(
        text(
            "INSERT INTO risk_decisions "
            "(account_id, side, qty, order_type, time_in_force, request_id, "
            "verdict, blockers, warnings, latency_ms, attempt_kind) "
            "VALUES (:aid, 'buy', 1, 'MARKET', 'DAY', :rid, "
            "'warn', '[]'::jsonb, '[]'::jsonb, 1, 'preview')"
        ),
        {"aid": aid, "rid": str(uuid4())},
    )
    count = (
        await s.execute(
            text(
                "SELECT count(*) FROM risk_decisions "
                "WHERE account_id = :aid AND attempt_kind = 'preview'"
            ),
            {"aid": aid},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_0043_downgrade_round_trip() -> None:
    """Downgrade narrows the CHECK; upgrade restores.

    Drives the migration through alembic.command on its own DBAPI
    connection (via asyncio.to_thread, mirroring test_alembic_0019 to
    avoid clashing with the pytest event loop's asyncpg connections),
    then inspects the constraint definition via a short-lived asyncpg
    connection. Re-runs head at the end to leave the DB canonical.
    """
    import asyncpg as _asyncpg

    cfg = _alembic_config()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _constraint_def() -> str:
        conn = await _asyncpg.connect(dsn)
        try:
            return await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'risk_decisions_attempt_kind_check'"
            )
        finally:
            await conn.close()

    try:
        await asyncio.to_thread(command.downgrade, cfg, "0042_phase11a_ai_jobs")
        narrow = await _constraint_def()
        assert "'preview'" not in narrow
        assert "'place_order'" in narrow
        assert "'modify_order'" in narrow
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        wide = await _constraint_def()
        assert "'preview'" in wide
