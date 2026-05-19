from __future__ import annotations

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.config import settings


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.config_file_name = None
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


@pytest.mark.asyncio
async def test_0063_up_creates_advisor_decisions_table(db_session: AsyncSession) -> None:
    """bot_advisor_decisions exists after 0063."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'bot_advisor_decisions' ORDER BY ordinal_position"
        )
    )
    cols = [r[0] for r in result]
    assert "id" in cols
    assert "bot_id" in cols
    assert "verdict" in cols
    assert "account_gate_outcome" in cols
    assert "effective_mode" in cols
    assert "ai_completion_ts" in cols
    assert "ai_completion_request_id" in cols


@pytest.mark.asyncio
async def test_0063_up_bots_advisor_config_column(db_session: AsyncSession) -> None:
    """bots.advisor_config JSONB column exists with NOT NULL DEFAULT."""
    result = await db_session.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='bots' AND column_name='advisor_config'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert "OFF" in (row[0] or "")


@pytest.mark.asyncio
async def test_0063_up_bot_accounts_advisor_config_override_nullable(
    db_session: AsyncSession,
) -> None:
    """bot_accounts.advisor_config_override is JSONB, nullable, no default."""
    result = await db_session.execute(
        text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name='bot_accounts' AND column_name='advisor_config_override'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "YES"
    assert row[1] is None


@pytest.mark.asyncio
async def test_0063_up_stop_reason_check_includes_advisor_auto_pause(
    db_session: AsyncSession,
) -> None:
    """bot_runs_stop_reason_check allows advisor_auto_pause."""
    result = await db_session.execute(
        text(
            "SELECT check_clause FROM information_schema.check_constraints "
            "WHERE constraint_name = 'bot_runs_stop_reason_check'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert "advisor_auto_pause" in row[0]


@pytest.mark.asyncio
async def test_0063_up_index_bot_ts_exists(db_session: AsyncSession) -> None:
    """idx_bot_advisor_decisions_bot_ts exists."""
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='bot_advisor_decisions' "
            "AND indexname='idx_bot_advisor_decisions_bot_ts'"
        )
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_0063_up_no_fk_on_bot_run_id(db_session: AsyncSession) -> None:
    """bot_run_id has no FK constraint (hypertable retention)."""
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu "
            "    ON rc.constraint_name = kcu.constraint_name "
            "WHERE kcu.table_name = 'bot_advisor_decisions' "
            "AND kcu.column_name = 'bot_run_id'"
        )
    )
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_0063_up_no_fk_on_ai_completion_columns(db_session: AsyncSession) -> None:
    """ai_completion_ts + ai_completion_request_id have no FK (hypertable composite PK)."""
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu "
            "    ON rc.constraint_name = kcu.constraint_name "
            "WHERE kcu.table_name = 'bot_advisor_decisions' "
            "AND kcu.column_name IN ('ai_completion_ts', 'ai_completion_request_id')"
        )
    )
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_0063_up_bot_id_fk_is_restrict(db_session: AsyncSession) -> None:
    """bot_advisor_decisions.bot_id FK is ON DELETE RESTRICT."""
    result = await db_session.execute(
        text(
            "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu "
            "    ON rc.constraint_name = kcu.constraint_name "
            "WHERE kcu.table_name = 'bot_advisor_decisions' "
            "AND kcu.column_name = 'bot_id'"
        )
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "RESTRICT"


@pytest.mark.asyncio
async def test_0063_up_account_gate_outcome_check_values(db_session: AsyncSession) -> None:
    """account_gate_outcome CHECK covers all expected values."""
    result = await db_session.execute(
        text(
            "SELECT check_clause FROM information_schema.check_constraints "
            "WHERE constraint_name LIKE '%account_gate_outcome%'"
        )
    )
    row = result.fetchone()
    assert row is not None
    clause = row[0]
    for val in ("approved", "warned", "blocked", "not_evaluated", "error"):
        assert val in clause


def test_0063_up_down_up_clean() -> None:
    """Migration is reversible: up → down → up without error."""
    cfg = _alembic_config()
    command.upgrade(cfg, "0063")
    command.downgrade(cfg, "0062")
    command.upgrade(cfg, "0063")
