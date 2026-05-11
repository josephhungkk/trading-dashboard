"""Phase 10a.5 A1: alembic 0037 contract verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytest_plugins = ("tests.fixtures.db_session",)


@pytest.mark.asyncio
async def test_0037_pnl_intraday_table_exists(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'pnl_intraday'")
    )
    columns = {row.column_name for row in result}
    expected = {
        "account_id",
        "day_start_utc",
        "realized_today",
        "unrealized",
        "currency",
        "summary_updated_at",
        "updated_at",
        "source_label",
    }
    assert columns.issuperset(expected), f"missing: {expected - columns}"


@pytest.mark.asyncio
async def test_0037_currency_check_constraint(session: AsyncSession) -> None:
    """CHECK (currency ~ '^[A-Z]{3}$') rejects lowercase. Uses begin_nested
    so the constraint violation doesn't poison the outer transaction.
    """
    s = session
    aid = uuid4()
    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, account_number, mode, gateway_label, "
            " currency_base, last_seen_via) "
            "VALUES (:id, 'ibkr', :acct_num, 'paper', 'isa-paper', "
            " 'USD', 'isa-paper') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": aid, "acct_num": f"test-{aid.hex[:8]}"},
    )

    with pytest.raises(Exception) as exc:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO pnl_intraday "
                    "(account_id, day_start_utc, realized_today, unrealized, "
                    " currency, summary_updated_at, source_label) "
                    "VALUES (:account_id, :day_start_utc, :realized_today, "
                    " :unrealized, :currency, :summary_updated_at, :source_label)"
                ),
                {
                    "account_id": aid,
                    "day_start_utc": datetime.now(UTC).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ),
                    "realized_today": Decimal("0"),
                    "unrealized": Decimal("0"),
                    "currency": "usd",
                    "summary_updated_at": datetime.now(UTC),
                    "source_label": "test",
                },
            )
    assert "ck_pnl_intraday_currency_iso3" in str(exc.value)


@pytest.mark.asyncio
async def test_0037_view_returns_zero_rows_when_empty(
    db_session: AsyncSession,
) -> None:
    """CRIT-2: view returns 0 rows when pnl_intraday is empty (no LEFT JOIN).

    risk_service.py sees row=None -> WARN with code max_daily_loss_pnl_stale.
    """
    result = await db_session.execute(text("SELECT * FROM v_account_intraday_pnl"))
    assert result.all() == []


@pytest.mark.asyncio
async def test_0037_view_exposes_staleness(session: AsyncSession) -> None:
    """View exposes `staleness` column = now() - summary_updated_at."""
    s = session
    aid = uuid4()
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    summary_updated_at = now - timedelta(seconds=120)

    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, account_number, mode, gateway_label, "
            " currency_base, last_seen_via) "
            "VALUES (:id, 'ibkr', :acct_num, 'paper', 'isa-paper', "
            " 'USD', 'isa-paper') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": aid, "acct_num": f"test-{aid.hex[:8]}"},
    )

    await s.execute(
        text(
            "INSERT INTO pnl_intraday "
            "(account_id, day_start_utc, realized_today, unrealized, "
            " currency, summary_updated_at, source_label) "
            "VALUES (:account_id, :day_start_utc, :realized_today, "
            " :unrealized, :currency, :summary_updated_at, :source_label)"
        ),
        {
            "account_id": aid,
            "day_start_utc": today_start,
            "realized_today": Decimal("100"),
            "unrealized": Decimal("50"),
            "currency": "USD",
            "summary_updated_at": summary_updated_at,
            "source_label": "ibkr",
        },
    )

    result = await s.execute(
        text(
            "SELECT realized, unrealized, staleness "
            "FROM v_account_intraday_pnl WHERE account_id = :account_id"
        ),
        {"account_id": aid},
    )
    row = result.one()
    assert row.realized == Decimal("100")
    assert row.unrealized == Decimal("50")
    assert row.staleness.total_seconds() >= 119


@pytest.mark.asyncio
async def test_0037_idx_risk_decisions_verdict_time(
    db_session: AsyncSession,
) -> None:
    """HIGH-4 index for verdict-filtered admin feed reads."""
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'risk_decisions' "
            "  AND indexname = 'idx_risk_decisions_verdict_time'"
        )
    )
    assert len(result.all()) == 1


@pytest.mark.asyncio
async def test_0037_prune_risk_decisions_allow(session: AsyncSession) -> None:
    """prune_risk_decisions_allow(30) deletes ALLOW > 30d, keeps WARN/BLOCK."""
    s = session
    aid = uuid4()
    now = datetime.now(UTC)
    old_allow = now - timedelta(days=45)
    young_allow = now - timedelta(days=10)
    old_block = now - timedelta(days=45)

    await s.execute(
        text(
            "INSERT INTO broker_accounts "
            "(id, broker_id, account_number, mode, gateway_label, "
            " currency_base, last_seen_via) "
            "VALUES (:id, 'ibkr', :acct_num, 'paper', 'isa-paper', "
            " 'USD', 'isa-paper') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": aid, "acct_num": f"test-{aid.hex[:8]}"},
    )

    await s.execute(
        text(
            "INSERT INTO risk_decisions "
            "(account_id, side, qty, order_type, time_in_force, "
            " verdict, evaluated_at, latency_ms, attempt_kind, request_id) "
            "VALUES (:account_id, 'buy', 1, 'MKT', 'DAY', "
            " :verdict, :evaluated_at, 0, 'place_order', :request_id)"
        ),
        [
            {
                "account_id": aid,
                "verdict": "allow",
                "evaluated_at": old_allow,
                "request_id": str(uuid4()),
            },
            {
                "account_id": aid,
                "verdict": "allow",
                "evaluated_at": young_allow,
                "request_id": str(uuid4()),
            },
            {
                "account_id": aid,
                "verdict": "block",
                "evaluated_at": old_block,
                "request_id": str(uuid4()),
            },
        ],
    )

    result = await s.execute(
        text("SELECT prune_risk_decisions_allow(:retain_days)"),
        {"retain_days": 30},
    )
    deleted_count = result.scalar_one()
    assert deleted_count == 1
