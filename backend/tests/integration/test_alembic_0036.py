"""Phase 10a Alembic 0036 migration tests.

Verifies the post-0036 schema:
- 5 tables: risk_limits, risk_limits_history, account_kill_switches,
  account_kill_switches_history, risk_decisions
- 3 enums: risk_scope_type, risk_limit_kind, risk_verdict
- 2 partial unique indexes on risk_limits (NULL-safe scope_id; C1 fix)
- UPDATE trigger snapshots OLD row into risk_limits_history (M3)
- AFTER INSERT pg_notify trigger on risk_decisions emits minimal
  payload {id, verdict, account_id} (M4)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_0036_creates_all_tables(db_session: AsyncSession) -> None:
    rows = (
        (
            await db_session.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' "
                    "AND tablename = ANY(:names) ORDER BY tablename"
                ),
                {
                    "names": [
                        "account_kill_switches",
                        "account_kill_switches_history",
                        "risk_decisions",
                        "risk_limits",
                        "risk_limits_history",
                    ]
                },
            )
        )
        .scalars()
        .all()
    )
    assert rows == [
        "account_kill_switches",
        "account_kill_switches_history",
        "risk_decisions",
        "risk_limits",
        "risk_limits_history",
    ]


@pytest.mark.asyncio
async def test_0036_creates_enums(db_session: AsyncSession) -> None:
    rows = (
        (
            await db_session.execute(
                text("SELECT typname FROM pg_type WHERE typname = ANY(:names) ORDER BY typname"),
                {"names": ["risk_limit_kind", "risk_scope_type", "risk_verdict"]},
            )
        )
        .scalars()
        .all()
    )
    assert rows == ["risk_limit_kind", "risk_scope_type", "risk_verdict"]


@pytest.mark.asyncio
async def test_0036_global_unique_blocks_duplicate(session: AsyncSession) -> None:
    """C1: two `(global, NULL, max_daily_loss_currency_base)` rows must fail.

    Plain UNIQUE treats NULLs as distinct in Postgres. The migration
    replaces the single UNIQUE with two partial indexes (uq_global_kind +
    uq_scoped) so global rows are deduped on `limit_kind` alone.
    """
    s = session
    await s.execute(
        text(
            "INSERT INTO risk_limits "
            "(scope_type, scope_id, limit_kind, limit_value, updated_by) "
            "VALUES ('global', NULL, 'max_daily_loss_currency_base', 1000, 'test')"
        )
    )
    with pytest.raises(IntegrityError):
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO risk_limits "
                    "(scope_type, scope_id, limit_kind, limit_value, updated_by) "
                    "VALUES ('global', NULL, 'max_daily_loss_currency_base', 2000, 'test')"
                )
            )


@pytest.mark.asyncio
async def test_0036_history_trigger_fires(session: AsyncSession) -> None:
    """M3: UPDATE on risk_limits writes a history row with OLD values."""
    s = session
    inserted = await s.execute(
        text(
            "INSERT INTO risk_limits "
            "(scope_type, scope_id, limit_kind, limit_value, updated_by) "
            "VALUES ('global', NULL, 'pdt_warn_remaining', 1, 'op1') RETURNING id"
        )
    )
    rid = inserted.scalar_one()
    await s.execute(
        text("UPDATE risk_limits SET limit_value = 2, updated_by = 'op2' WHERE id = :id"),
        {"id": rid},
    )
    history = (
        await s.execute(
            text(
                "SELECT limit_value::text, changed_by FROM risk_limits_history WHERE limit_id = :id"
            ),
            {"id": rid},
        )
    ).all()
    assert len(history) == 1
    # OLD.limit_value = 1 snapshotted; NEW.updated_by = 'op2' as changed_by
    assert history[0][0] == "1.00000000"
    assert history[0][1] == "op2"


@pytest.mark.asyncio
async def test_0036_pg_notify_trigger_function_exists(
    db_session: AsyncSession,
) -> None:
    """M4: fn_risk_decisions_notify references pg_notify with minimal payload."""
    src = (
        await db_session.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'fn_risk_decisions_notify'")
        )
    ).scalar_one_or_none()
    assert src is not None
    assert "pg_notify" in src
    assert "risk_decision" in src
    # Minimal payload — no blockers / warnings JSONB inlined (M4 8KB cap)
    assert "blockers" not in src
    assert "warnings" not in src


@pytest.mark.asyncio
async def test_0036_account_kill_switch_history_trigger(
    session: AsyncSession,
) -> None:
    """Symmetric history trigger for account_kill_switches."""
    s = session
    # account_kill_switches FK requires a real broker_accounts row; create one
    aid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, gateway_label, account_number, alias, mode, "
            "currency_base, display_order, first_seen_at, last_seen_at, "
            "last_seen_via) "
            "VALUES (:id, 'ibkr', 'phase10a-test', 'A0001', 'phase10a-test', "
            "'paper', 'USD', 0, now(), now(), 'phase10a-test')"
        ),
        {"id": aid},
    )
    await s.execute(
        text(
            "INSERT INTO account_kill_switches "
            "(account_id, is_enabled, reason, enabled_at, enabled_by) "
            "VALUES (:aid, TRUE, 'first', now(), 'op1')"
        ),
        {"aid": aid},
    )
    await s.execute(
        text(
            "UPDATE account_kill_switches SET reason = 'second', "
            "enabled_by = 'op2' WHERE account_id = :aid"
        ),
        {"aid": aid},
    )
    history = (
        await s.execute(
            text(
                "SELECT reason, changed_by FROM account_kill_switches_history "
                "WHERE account_id = :aid"
            ),
            {"aid": aid},
        )
    ).all()
    assert len(history) == 1
    assert history[0][0] == "first"  # OLD value
    assert history[0][1] == "op2"


@pytest.mark.asyncio
async def test_0036_creates_intraday_pnl_view(db_session: AsyncSession) -> None:
    """B3 [M2]: v_account_intraday_pnl exists and yields the contract columns.

    Phase 10a.5 0037 rewrote the view to read from pnl_intraday and added
    summary_updated_at + staleness_s. The Phase 10a stub (zero realized /
    unrealized for every account) is gone; the view only returns rows where
    pnl_intraday has an entry for the current UTC day.
    """
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'v_account_intraday_pnl' "
                    "ORDER BY ordinal_position"
                )
            )
        )
        .scalars()
        .all()
    )
    assert cols == [
        "account_id",
        "day_start_utc",
        "realized",
        "unrealized",
        "summary_updated_at",
        "staleness_s",
    ]
